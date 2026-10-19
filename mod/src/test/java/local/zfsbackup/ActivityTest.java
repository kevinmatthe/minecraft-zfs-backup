package local.zfsbackup;
import java.util.concurrent.CompletableFuture;
/** The optional activity policy applies to natural live captures, never independent hourly saves. */
public final class ActivityTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static CheckpointTest.Rig live(boolean onlyIfActive){var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,onlyIfActive,true);return r;}
 static void success(CheckpointTest.Rig r){r.ack("SNAPSHOT_READY");r.ack("COMMITTED");}
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("initial empty server skips natural live checkpoint when idle filter enabled",()->{var r=live(true);r.advance(3600000);check(!r.c.autosave()&&r.events.isEmpty(),"idle live capture must not prepare");});
  test("manual works without players",()->{var r=live(true);r.advance(3600000);r.start();check(r.freezes==1,"manual bypass");success(r);});
  test("activity remains after last player leaves",()->{var r=live(true);r.c.playerActivity();r.advance(3600000);check(r.c.autosave()&&r.events.contains("prepare:periodic_save"),"past activity permits fresh natural batch");});
  test("only COMMITTED clears captured activity",()->{var r=live(true);r.c.playerActivity();r.start();success(r);r.advance(3600000);check(!r.c.autosave()&&r.freezes==1&&!r.c.wantsShutdownSnapshot(true),"success clean for live activity policy");});
  test("failure preserves activity for a later natural batch",()->{var r=live(true);r.c.playerActivity();r.start();r.ack("ERROR");check(r.c.wantsShutdownSnapshot(true),"failed dirty");r.prepared=new CompletableFuture<>();r.ready=new CompletableFuture<>();r.committed=new CompletableFuture<>();r.advance(60000);check(r.c.autosave()&&r.events.stream().filter(e->e.startsWith("prepare:")).count()==2,"dirty live capture resumes on a fresh batch after backoff");});
  test("in-flight activity survives success",()->{var r=live(true);r.c.playerActivity();r.start();r.c.playerActivity();success(r);check(r.c.wantsShutdownSnapshot(true),"new generation dirty");});
  test("shutdown snapshot respects flags and activity",()->{var r=live(true);check(!r.c.wantsShutdownSnapshot(true),"idle shutdown skip");r.c.playerActivity();r.c.shutdown();check(r.c.wantsShutdownSnapshot(true)&&!r.c.wantsShutdownSnapshot(false),"stopped preserves activity and config");});
  test("live activity gate can be disabled",()->{var r=live(false);check(r.c.autosave()&&r.events.contains("prepare:periodic_save")&&r.c.wantsShutdownSnapshot(true),"idle live gate disabled");});
  System.out.println(passed+" player activity tests passed");
 }
}
