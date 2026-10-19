package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
public final class CheckpointTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static final class Rig implements Checkpoint.Port {
  long time=100000,elapsed=1000,busyDelay;boolean frozen,busy,failSave,queueCallbacks;
  Queue<Runnable> callbacks=new ArrayDeque<>();int freezes,restores;String snapshot="mc-live-20261019T060000Z";
  Checkpoint.Request request;List<String> events=new ArrayList<>();
  CompletableFuture<Checkpoint.Reply> prepared=new CompletableFuture<>(),ready=new CompletableFuture<>(),committed=new CompletableFuture<>();
  CompletableFuture<Void> barrier=CompletableFuture.completedFuture(null);
  Checkpoint c=new Checkpoint(this,()->time,()->elapsed,3600000,15000,true);
  public CompletionStage<Checkpoint.Reply> prepare(Checkpoint.Request r){request=r;events.add("prepare:"+r.event());return prepared;}
  public CompletionStage<Checkpoint.Reply> snapshot(Checkpoint.Request r,long frozenAt){check(frozen,"snapshot requires disabled saving");events.add("snapshot");return ready;}
  public CompletionStage<Checkpoint.Reply> commit(Checkpoint.Request r,long committedAt){check(!frozen,"restore before commit");events.add("commit");return committed;}
  public CompletionStage<?> cancel(Checkpoint.Request r,long cancelledAt){events.add("cancel");return CompletableFuture.completedFuture(null);}
  public boolean busy(){time+=busyDelay;elapsed+=busyDelay;return busy;}
  public void freeze(){frozen=true;freezes++;events.add("freeze");}
  public void flush(){events.add("flush");if(failSave)throw new IllegalStateException("save failed");}
  public CompletionStage<Void> barrier(){return barrier;}
  public void restore(){frozen=false;restores++;events.add("restore");}
  public void execute(Runnable action){if(queueCallbacks)callbacks.add(action);else action.run();}
  Checkpoint.Reply reply(String state){return new Checkpoint.Reply(request.id(),state,time,snapshot);}
  void prepared(){prepared.complete(reply("PREPARED"));}
  void start(){check(c.now(),"manual start accepted");prepared();}
  void ack(String state){if(state.equals("SNAPSHOT_READY"))ready.complete(reply(state));else if(state.equals("COMMITTED"))committed.complete(reply(state));else ready.completeExceptionally(new IllegalStateException("HTTP503"));}
  void advance(long milliseconds){time+=milliseconds;elapsed+=milliseconds;c.tick();}
 }
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("prepare completes before saving disabled",()->{Rig r=new Rig();check(r.c.now(),"accepted");check(!r.frozen&&r.events.equals(List.of("prepare:manual_save")),"no freeze before readiness");r.prepared();check(r.frozen&&r.events.contains("snapshot"),"prepared permits checkpoint");});
  test("legacy timed complete-save mode uses hourly event and bypasses idle",()->{Rig r=new Rig();r.advance(3600000);check(r.events.equals(List.of("prepare:hourly_save")),"timed complete save is hourly even without player activity");r.prepared();check(r.frozen,"timer due");});
  test("prepare error never freezes",()->{Rig r=new Rig();r.c.now();r.prepared.completeExceptionally(new IllegalStateException("unavailable"));check(r.freezes==0&&r.events.contains("cancel"),"cancel preparation failure");check(!r.c.now(),"failure backoff");});
  test("save failure restores",()->{Rig r=new Rig();r.failSave=true;r.start();check(!r.frozen&&r.restores==1&&r.events.contains("cancel"),"failed save restored");});
  test("asynchronous barrier never blocks ticks",()->{Rig r=new Rig();r.barrier=new CompletableFuture<>();r.start();r.c.tick();check(r.frozen&&!r.events.contains("snapshot"),"barrier pending");r.barrier.complete(null);check(r.events.contains("snapshot"),"snapshot after barrier");});
  test("barrier failure restores",()->{Rig r=new Rig();r.barrier=CompletableFuture.failedFuture(new Exception("IO"));r.start();check(!r.frozen&&r.events.contains("cancel"),"barrier failure");});
  test("restore precedes commit and COMMITTED records success",()->{Rig r=new Rig();r.start();r.ack("SNAPSHOT_READY");check(!r.frozen&&r.events.indexOf("restore")<r.events.indexOf("commit"),"restore before commit");r.ack("COMMITTED");check(r.c.status().contains(r.snapshot),"success exposed");});
  test("mismatching preparation fails closed",()->{Rig r=new Rig();r.c.now();r.prepared.complete(new Checkpoint.Reply(UUID.randomUUID(),"PREPARED",r.time,null));check(r.freezes==0&&r.events.contains("cancel"),"wrong id cannot freeze");});
  test("wrong phase cannot commit",()->{Rig r=new Rig();r.start();r.ready.complete(r.reply("COMMITTED"));check(!r.frozen&&!r.events.contains("commit")&&r.events.contains("cancel"),"wrong phase restored");});
  test("timeout includes HTTP preparation",()->{Rig r=new Rig();r.c.now();r.advance(15000);r.prepared();check(r.freezes==0&&r.events.contains("cancel"),"late prepared callback ignored");});
  test("timeout includes barrier",()->{Rig r=new Rig();r.barrier=new CompletableFuture<>();r.start();r.advance(15000);r.barrier.complete(null);check(!r.frozen&&!r.events.contains("snapshot")&&r.events.contains("cancel"),"late barrier harmless");});
  test("snapshot HTTP timeout restores",()->{Rig r=new Rig();r.start();r.advance(15000);r.ack("SNAPSHOT_READY");check(!r.frozen&&!r.events.contains("commit")&&r.events.contains("cancel"),"late snapshot cannot commit");});
  test("commit acknowledgement may follow freeze deadline after restoring",()->{Rig r=new Rig();r.start();r.advance(14900);r.ack("SNAPSHOT_READY");r.advance(2000);check(!r.frozen&&!r.events.contains("cancel"),"commit wait has restored saving");r.ack("COMMITTED");check(r.c.status().contains(r.snapshot),"late commit acknowledgement records success");});
  test("commit HTTP failure cancels with saving enabled",()->{Rig r=new Rig();r.start();r.ack("SNAPSHOT_READY");r.committed.completeExceptionally(new Exception("HTTP500"));check(!r.frozen&&r.events.contains("cancel"),"commit failed");});
  test("clock rollback does not extend deadline",()->{Rig r=new Rig();r.start();r.time-=60000;r.elapsed+=15000;r.c.tick();check(!r.frozen&&r.events.contains("cancel"),"monotonic bound");});
  test("shutdown cancels restores and ignores callbacks",()->{Rig r=new Rig();r.start();r.c.shutdown();r.ack("SNAPSHOT_READY");check(!r.frozen&&r.events.contains("cancel")&&!r.events.contains("commit")&&!r.c.now(),"shutdown safe");});
  test("FTB busy defers before prepare",()->{Rig r=new Rig();r.busy=true;check(!r.c.now()&&r.events.isEmpty(),"FTB busy");});
  test("slow optional readiness check cannot freeze after deadline",()->{Rig r=new Rig();r.c.now();r.busyDelay=15000;r.prepared();check(r.freezes==0&&r.events.contains("cancel"),"expired prepare must not freeze");});
  test("FTB becomes active during prepare",()->{Rig r=new Rig();r.c.now();r.busy=true;r.prepared();check(!r.frozen&&r.events.contains("cancel"),"FTB recheck");});
  test("callbacks mutate only through the server executor",()->{Rig r=new Rig();r.queueCallbacks=true;r.c.now();r.prepared();check(r.freezes==0,"HTTP completion cannot mutate game state directly");r.callbacks.remove().run();check(r.frozen,"server callback owns freeze");r.c.shutdown();r.callbacks.remove().run();check(!r.events.contains("snapshot"),"queued barrier callback ignored after shutdown");});
  test("stale callback cannot affect a newer request",()->{Rig r=new Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,true,true);r.c.now();var oldRequest=r.request;var oldPrepared=r.prepared;r.advance(15000);r.advance(60000);r.prepared=new CompletableFuture<>();r.ready=new CompletableFuture<>();check(r.c.now(),"new attempt after backoff");oldPrepared.complete(new Checkpoint.Reply(oldRequest.id(),"PREPARED",r.time,null));check(r.freezes==0,"old request cannot freeze newer operation");r.prepared();check(r.freezes==1,"current preparation works");});
  test("no overlapping requests",()->{Rig r=new Rig();r.c.now();check(!r.c.now()&&r.events.size()==1,"single active request");});
  System.out.println(passed+" HTTP lifecycle tests passed");
 }
}
