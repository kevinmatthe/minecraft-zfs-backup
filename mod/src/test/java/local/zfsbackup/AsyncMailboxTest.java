package local.zfsbackup;

import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Real FIFO paths must never hold a server tick hostage while saving is disabled. */
public final class AsyncMailboxTest {
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 static void tick(ExecutorService server,Checkpoint checkpoint)throws Exception{
  try{server.submit(checkpoint::tick).get(1,TimeUnit.SECONDS);}
  catch(TimeoutException e){throw new AssertionError("server tick blocked on FIFO; save timeout cannot run",e);}
 }
 public static void main(String[] args)throws Exception{
  if(!System.getProperty("os.name").equals("Linux")){System.out.println("SKIP real FIFO regression: Linux required");return;}
  Path directory=Files.createTempDirectory("zfs-fifo-regression-");
  ExecutorService server=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"test-server");t.setDaemon(true);return t;});
  try{
   Path heartbeat=directory.resolve("heartbeat.json");
   Files.writeString(heartbeat,"{\"protocol\":1,\"updated_ms\":100000,\"ready\":true}");
   Path response=directory.resolve("response.json");
   check(new ProcessBuilder("mkfifo",response.toString()).start().waitFor()==0,"create isolated FIFO");
   try(AsyncMailbox mailbox=new AsyncMailbox(new Mailbox(directory))){
    AtomicLong now=new AtomicLong(100000);AtomicBoolean frozen=new AtomicBoolean();List<Checkpoint.Request> sent=new ArrayList<>();
    Checkpoint checkpoint=new Checkpoint(new Checkpoint.Port(){
     public Checkpoint.Heartbeat heartbeat()throws Exception{return mailbox.heartbeat();}
     public Checkpoint.Response response()throws Exception{return mailbox.response();}
     public void publish(Checkpoint.Request request)throws Exception{mailbox.publish(request);sent.add(request);}
     public boolean busy(){return false;}
     public void freeze(){frozen.set(true);}
     public void flush(){}
     public CompletionStage<Void> barrier(){return CompletableFuture.completedFuture(null);}
     public void restore(){frozen.set(false);}
     public void execute(Runnable action){action.run();}
    },now::get,3600000,15000);
    long waitUntil=System.nanoTime()+TimeUnit.SECONDS.toNanos(3);
    while(mailbox.heartbeat()==null&&System.nanoTime()<waitUntil)Thread.sleep(10);
    check(checkpoint.now(),"fresh heartbeat permits isolated checkpoint");
    check(frozen.get(),"checkpoint froze saving");
    tick(server,checkpoint);
    now.addAndGet(15000);tick(server,checkpoint);
    check(!frozen.get(),"saving restored despite blocked FIFO reader");
    check(sent.getLast().phase().equals("CANCEL"),"timeout cancellation published");
    checkpoint.shutdown();
   }
   System.out.println("PASS FIFO response cannot block tick or prevent timeout restoration");
   Files.delete(heartbeat);
   check(new ProcessBuilder("mkfifo",heartbeat.toString()).start().waitFor()==0,"create isolated heartbeat FIFO");
   try(AsyncMailbox mailbox=new AsyncMailbox(new Mailbox(directory))){
    Thread.sleep(150); // Give both reader tasks time to enter FIFO open(2).
    check(server.submit(mailbox::heartbeat).get(1,TimeUnit.SECONDS)==null,"FIFO heartbeat remains unavailable without blocking server");
   }
   System.out.println("PASS FIFO heartbeat is unavailable without blocking server");
  }finally{
   server.shutdownNow();
   try(var paths=Files.list(directory)){for(Path path:paths.toList())Files.delete(path);}
   Files.delete(directory);
  }
 }
}
