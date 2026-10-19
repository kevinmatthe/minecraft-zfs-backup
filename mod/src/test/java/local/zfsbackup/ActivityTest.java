package local.zfsbackup;

/** Player activity belongs to the successful checkpoint generation, not merely its attempt. */
public final class ActivityTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static void due(CheckpointTest.Rig r,long delay){r.time+=delay;r.heartbeat=r.time;r.c.tick();}
 static void success(CheckpointTest.Rig r){r.ack("SNAPSHOT_READY");r.ack("COMMITTED");}
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("initial empty server skips automatic checkpoint",()->{var r=new CheckpointTest.Rig();due(r,3600000);check(r.freezes==0&&r.sent.isEmpty(),"no player activity must not freeze or publish");});
  test("manual checkpoint works without players",()->{var r=new CheckpointTest.Rig();due(r,3600000);r.start();check(r.freezes==1,"manual bypasses idle gate");success(r);});
  test("activity remains after the last player leaves",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();due(r,3600000);check(r.freezes==1,"prior activity triggers next interval without online players");});
  test("only COMMITTED clears captured activity",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();success(r);due(r,3600000);check(r.freezes==1,"successful captured activity is clean");});
  test("failed checkpoint preserves activity",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();r.ack("ERROR");due(r,60000);check(r.freezes==2,"failure must retry dirty generation");});
  test("SNAPSHOT_READY alone cannot clear activity",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();r.ack("SNAPSHOT_READY");due(r,15000);due(r,60000);check(r.freezes==2,"missing COMMITTED retains dirty generation");});
  test("activity during checkpoint survives success",()->{var r=new CheckpointTest.Rig();r.c.playerActivity();r.start();r.c.playerActivity();success(r);due(r,3600000);check(r.freezes==2,"in-flight activity belongs to the next checkpoint");});
  test("idle filter can be disabled",()->{var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.time,3600000,15000,false);due(r,3600000);check(r.freezes==1,"disabled filter permits empty automatic checkpoints");});
  System.out.println(passed+" player activity tests passed");
 }
}
