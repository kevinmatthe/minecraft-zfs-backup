package local.zfsbackup;

import java.util.concurrent.*;

/** Behavioral checks for observing a completed save, with no second serialization. */
public final class AutosaveCheckpointTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static CheckpointTest.Rig rig(){
  var r=new CheckpointTest.Rig();
  r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000L,15000L,false,true);
  return r;
 }
 static boolean autosave(CheckpointTest.Rig r){
  return r.c.autosave();
 }
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("live coordinator does not itself synthesize timed saves; hourly scheduler is separate",()->{var r=rig();r.advance(86400000);check(r.events.isEmpty(),"the live coordinator alone does not own hourly scheduling");});
  test("completed autosave freezes immediately but does not serialize again",()->{var r=rig();check(autosave(r),"autosave accepted without players");check(r.frozen&&r.freezes==1,"capture saving flags before asynchronous preparation");check(!r.events.contains("flush"),"no duplicate save before preparation");r.prepared();check(!r.events.contains("flush")&&r.events.contains("snapshot"),"snapshot uses the already captured save");});
  test("one active autosave cannot create a second checkpoint",()->{var r=rig();check(autosave(r),"first");check(!autosave(r)&&r.freezes==1,"no overlapping capture");r.prepared();r.ack("SNAPSHOT_READY");r.ack("COMMITTED");check(r.restores==1&&!r.frozen,"original flags restored");});
  test("autosave waits asynchronously for all queued writes",()->{var r=rig();r.barrier=new CompletableFuture<>();autosave(r);r.prepared();r.c.tick();check(!r.events.contains("snapshot")&&r.frozen,"pending IO barrier");r.barrier.complete(null);check(r.events.contains("snapshot"),"snapshot follows completed writes");});
  test("autosave barrier failure restores and never snapshots",()->{var r=rig();r.barrier=CompletableFuture.failedFuture(new Exception("IO failed"));autosave(r);r.prepared();check(!r.frozen&&r.restores==1&&!r.events.contains("snapshot")&&r.events.contains("cancel"),"failed IO cannot publish");});
  test("preparation failure restores flags captured at autosave completion",()->{var r=rig();autosave(r);r.prepared.completeExceptionally(new Exception("HTTP unavailable"));check(!r.frozen&&r.restores==1&&r.events.contains("cancel"),"no stranded freeze");});
  test("autosave timeout restores and ignores late callbacks",()->{var r=rig();r.barrier=new CompletableFuture<>();autosave(r);r.prepared();r.advance(15000);r.barrier.complete(null);check(!r.frozen&&!r.events.contains("snapshot")&&r.events.contains("cancel"),"late barrier cannot snapshot");});
  test("live failures never serialize the same saved batch again",()->{var r=rig();autosave(r);r.advance(15000);r.advance(3600000);check(r.events.stream().filter(e->e.startsWith("prepare:")).count()==1,"retry waits for next actual save batch");});
  test("manual request still performs exactly one forced save",()->{var r=rig();r.start();check(r.events.stream().filter(e->e.equals("flush")).count()==1,"explicit save retains serialization");});
  test("shutdown restores an autosave frozen during preparation",()->{var r=rig();autosave(r);r.c.shutdown();r.prepared();check(!r.frozen&&r.restores==1&&!r.events.contains("snapshot"),"stopped autosave cannot resume");});
  test("external forced save cancels capture before writing",()->{var r=rig();autosave(r);r.prepared();r.c.externalSave();r.ack("SNAPSHOT_READY");check(!r.frozen&&r.restores==1&&r.events.contains("cancel")&&!r.events.contains("commit"),"late callback cannot publish a capture overlapping another save");});
  test("FTB backup overlap leaves existing save flags alone",()->{var r=rig();r.busy=true;check(!autosave(r)&&r.freezes==0&&r.events.isEmpty(),"busy save not frozen");});
  System.out.println(passed+" completed-autosave checks passed");
 }
}
