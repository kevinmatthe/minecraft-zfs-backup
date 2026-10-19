package local.zfsbackup;
public final class ActivityTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static void success(CheckpointTest.Rig r){r.ack("SNAPSHOT_READY");r.ack("COMMITTED");}
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("initial empty server skips automatic checkpoint",()->{var r=new CheckpointTest.Rig();r.advance(3600000);check(r.events.isEmpty(),"empty server must not prepare");});
  test("manual works without players",()->{var r=new CheckpointTest.Rig();r.advance(3600000);r.start();check(r.freezes==1,"manual bypass");success(r);});
  test("activity remains after last player leaves",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.advance(3600000);check(r.events.contains("prepare:periodic_save"),"past activity");});
  test("only COMMITTED clears captured activity",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();success(r);r.advance(3600000);check(r.freezes==1&&!r.c.wantsShutdownSnapshot(true),"success clean");});
  test("failure preserves activity",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();r.ack("ERROR");check(r.c.wantsShutdownSnapshot(true),"failed dirty");r.advance(60000);check(r.events.stream().filter(e->e.startsWith("prepare:")).count()==2,"dirty retry");});
  test("in-flight activity survives success",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();r.c.playerActivity();success(r);check(r.c.wantsShutdownSnapshot(true),"new generation dirty");});
  test("shutdown snapshot respects flags and activity",()->{var r=new CheckpointTest.Rig();check(!r.c.wantsShutdownSnapshot(true),"idle shutdown skip");r.c.playerActivity();r.c.shutdown();check(r.c.wantsShutdownSnapshot(true)&&!r.c.wantsShutdownSnapshot(false),"stopped preserves activity and config");});
  test("activity gate can be disabled",()->{var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,false);r.advance(3600000);check(r.events.contains("prepare:periodic_save")&&r.c.wantsShutdownSnapshot(true),"idle gate disabled");});
  System.out.println(passed+" player activity tests passed");
 }
}
