package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
public final class CheckpointTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static final class Rig implements Checkpoint.Port {
  long time=100_000,responseDelay,busyDelay; boolean frozen,busy,failSave,failCommit; int freezes,restores; long heartbeat=time;
  CompletableFuture<Void> barrier=CompletableFuture.completedFuture(null);
  List<Checkpoint.Request> sent=new ArrayList<>(); Checkpoint.Response reply;
  Checkpoint c=new Checkpoint(this,()->time,3600000,15000);
  public Checkpoint.Heartbeat heartbeat(){return new Checkpoint.Heartbeat(heartbeat,true);}
  public Checkpoint.Response response(){time+=responseDelay;return reply;}
  public void publish(Checkpoint.Request r){if(r.phase().equals("COMMIT")){check(!frozen,"saving must restore before COMMIT");if(failCommit)throw new IllegalStateException("disk full");}sent.add(r);}
  public boolean busy(){time+=busyDelay;return busy;}
  public void freeze(){frozen=true;freezes++;}
  public void flush(){if(failSave)throw new IllegalStateException("save failed");}
  public CompletionStage<Void> barrier(){return barrier;}
  public void restore(){frozen=false;restores++;}
  public void execute(Runnable r){r.run();}
  void start(){check(c.now(),"manual start accepted");c.tick();}
  Checkpoint.Request request(){return sent.getLast();}
  void ack(String phase){reply=new Checkpoint.Response(request().id(),phase,time,"mc-hourly-test");c.tick();}
 }
 static void test(String name,Runnable r){r.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("elapsed wall timer",()->{Rig r=new Rig();r.c.playerActivity();r.c.tick();check(r.freezes==0,"no startup checkpoint");r.time+=3600000;r.heartbeat=r.time;r.c.tick();check(r.freezes==1,"wall-time due");});
  test("stale and future heartbeat",()->{for(long offset:new long[]{-5001,1001}){Rig r=new Rig();r.heartbeat+=offset;r.c.now();check(r.freezes==0,"heartbeat must fail closed");}});
  test("heartbeat freshness checked immediately before freeze",()->{Rig r=new Rig();r.busyDelay=5001;r.c.now();check(!r.frozen,"reflection delay must not use stale heartbeat");});
  test("FTB defers",()->{Rig r=new Rig();r.busy=true;r.c.now();check(!r.frozen,"FTB active");});
  test("save failure restores and retries",()->{Rig r=new Rig();r.failSave=true;r.c.now();check(!r.frozen&&r.restores==1,"restore after failed save");check(r.request().phase().equals("CANCEL"),"cancel failed save");r.failSave=false;r.c.now();check(r.freezes==1,"retry backoff cannot be bypassed");});
  test("asynchronous barrier permits ticks",()->{Rig r=new Rig();r.barrier=new CompletableFuture<>();r.start();check(r.frozen&&r.sent.isEmpty(),"barrier pending");r.c.tick();r.barrier.complete(null);r.c.tick();check(r.request().phase().equals("FROZEN"),"publish after barrier");});
  test("barrier failure restores",()->{Rig r=new Rig();r.barrier=CompletableFuture.failedFuture(new Exception("IO"));r.c.now();r.c.tick();check(!r.frozen&&r.request().phase().equals("CANCEL"),"barrier failure");});
  test("matching ack restores before commit and records success",()->{Rig r=new Rig();r.start();r.ack("SNAPSHOT_READY");check(!r.frozen&&r.request().phase().equals("COMMIT"),"restore before commit");r.ack("COMMITTED");check(r.c.status().contains("mc-hourly-test"),"successful snapshot exposed");});
  test("wrong UUID and wrong phase ignored",()->{Rig r=new Rig();r.start();r.reply=new Checkpoint.Response(UUID.randomUUID(),"SNAPSHOT_READY",r.time,"bad");r.c.tick();check(r.frozen,"unrelated response ignored");r.ack("COMMITTED");check(r.frozen,"early COMMITTED ignored");});
  test("timeout includes barrier",()->{Rig r=new Rig();r.barrier=new CompletableFuture<>();r.start();r.time+=15000;r.c.tick();check(!r.frozen&&r.request().phase().equals("CANCEL"),"barrier bounded");r.barrier.complete(null);r.c.tick();check(r.request().phase().equals("CANCEL"),"late callback harmless");});
  test("host wait timeout",()->{Rig r=new Rig();r.start();r.time+=15000;r.c.tick();check(!r.frozen&&r.request().phase().equals("CANCEL"),"daemon disappearance restores saving");});
  test("commit publication failure",()->{Rig r=new Rig();r.start();r.failCommit=true;r.ack("SNAPSHOT_READY");check(!r.frozen&&r.request().phase().equals("CANCEL"),"failed commit cancel");});
  test("matching ERROR",()->{Rig r=new Rig();r.start();r.ack("ERROR");check(!r.frozen&&r.request().phase().equals("CANCEL"),"host failure");});
  test("shutdown restores and forbids new request",()->{Rig r=new Rig();r.start();r.c.shutdown();check(!r.frozen&&r.request().phase().equals("CANCEL"),"shutdown cancel");check(!r.c.now(),"no requests after shutdown");});
  test("transport delay cannot commit after deadline",()->{Rig r=new Rig();r.start();r.responseDelay=15001;r.ack("SNAPSHOT_READY");check(!r.frozen&&r.request().phase().equals("CANCEL"),"late transport must cancel");});
  test("clock rollback cannot prolong freeze",()->{Rig r=new Rig();long[] elapsed={1000};r.c=new Checkpoint(r,()->r.time,()->elapsed[0],3600000,15000);r.start();r.time-=60000;elapsed[0]+=15000;r.c.tick();check(!r.frozen,"monotonic freeze deadline");});
  test("no overlapping manual checkpoint",()->{Rig r=new Rig();r.start();check(!r.c.now()&&r.freezes==1,"single request");});
  System.out.println(passed+" lifecycle tests passed");
 }
}
