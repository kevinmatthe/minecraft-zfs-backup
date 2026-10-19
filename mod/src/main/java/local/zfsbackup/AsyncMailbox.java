package local.zfsbackup;

import java.io.IOException;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Two bounded read workers isolate server ticks from mailbox filesystem reads, including
 * FIFO open/read and path replacement races. Each file has only one scheduled reader.
 */
final class AsyncMailbox implements AutoCloseable {
 private record Result<T>(T value,IOException error) {}
 @FunctionalInterface private interface Read<T>{T get()throws IOException;}
 private final Mailbox mailbox;
 private final ScheduledExecutorService readers;
 private volatile Result<Checkpoint.Heartbeat> heartbeat=new Result<>(null,null);
 private volatile Result<Checkpoint.Response> response=new Result<>(null,null);
 AsyncMailbox(Mailbox mailbox){
  this.mailbox=mailbox;
  AtomicInteger sequence=new AtomicInteger();
  readers=Executors.newScheduledThreadPool(2,task->{
   Thread thread=new Thread(task,"zfs-mailbox-reader-"+sequence.incrementAndGet());
   // A malicious FIFO may hold open(2) indefinitely; it must never hold JVM shutdown.
   thread.setDaemon(true);return thread;
  });
  readers.scheduleWithFixedDelay(()->heartbeat=read(mailbox::heartbeat),0,100,TimeUnit.MILLISECONDS);
  readers.scheduleWithFixedDelay(()->response=read(mailbox::response),0,100,TimeUnit.MILLISECONDS);
 }
 private static <T> Result<T> read(Read<T> operation){
  try{return new Result<>(operation.get(),null);}
  catch(IOException e){return new Result<>(null,e);}
  catch(RuntimeException e){return new Result<>(null,new IOException("mailbox read failed",e));}
 }
 Checkpoint.Heartbeat heartbeat()throws IOException{
  Result<Checkpoint.Heartbeat> current=heartbeat;
  if(current.error()!=null)throw current.error();return current.value();
 }
 Checkpoint.Response response()throws IOException{
  Result<Checkpoint.Response> current=response;
  if(current.error()!=null)throw current.error();return current.value();
 }
 void publish(Checkpoint.Request request)throws IOException{mailbox.publish(request);}
 public void close(){readers.shutdownNow();}
}
