package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
public final class HourlySchedulerTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static final class Fixture {
  final CheckpointTest.Rig r=new CheckpointTest.Rig();final List<String> logs=new ArrayList<>();final HourlyScheduler scheduler;
  Fixture(){r.snapshot="mc-hourly-20261019T060000Z";r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,true,true);scheduler=new HourlyScheduler(r.c,()->r.time,()->r.elapsed,3600000,logs::add);}
  void tick(){scheduler.tick();r.c.tick();}
  void advance(long millis){r.time+=millis;r.elapsed+=millis;tick();}
  void renew(){r.prepared=new CompletableFuture<>();r.ready=new CompletableFuture<>();r.committed=new CompletableFuture<>();r.barrier=CompletableFuture.completedFuture(null);}
  void success(){r.prepared();r.ack("SNAPSHOT_READY");r.ack("COMMITTED");tick();}
  long hourlyRequests(){return r.events.stream().filter("prepare:hourly_save"::equals).count();}
 }
 static void test(String name,Runnable action){action.run();passed++;System.out.println("PASS "+name);}
 public static void main(String[] args){
  test("startup hourly baseline needs no autosave or player activity",()->{var f=new Fixture();f.tick();check(f.hourlyRequests()==1&&!f.r.frozen,"prepare full baseline independently before freezing");f.r.prepared();check(f.r.frozen&&Collections.frequency(f.r.events,"flush")==1,"hourly serializes complete save exactly once");});
  test("hourly committed schedules next UTC boundary",()->{var f=new Fixture();f.tick();f.success();check(!f.scheduler.pending()&&f.scheduler.nextDueWallMillis()==3600000,"next UTC boundary rather than success plus one hour");f.renew();f.advance(3499999);check(f.hourlyRequests()==1,"not yet due");f.advance(1);check(f.hourlyRequests()==2,"wall-hour checkpoint without any natural autosave");});
  test("frequent live and manual success never postpones hourly",()->{var f=new Fixture();f.tick();f.success();long due=f.scheduler.nextDueWallMillis();for(int i=0;i<4;i++){f.advance(500000);f.renew();f.r.c.playerActivity();f.r.snapshot=(i%2==0?"mc-live-":"mc-hourly-")+"20261019T060000Z";check(i%2==0?f.r.c.autosave():f.r.c.now(),"non-hourly work accepted before due");f.success();check(f.scheduler.nextDueWallMillis()==due,"independent due survives other successes");}f.renew();f.advance(due-f.r.time);check(f.hourlyRequests()==2,"hourly remains punctual");});
  test("pending hourly waits for active manual then takes priority over new live",()->{var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,false,true);check(r.c.now(),"active manual");var scheduler=new HourlyScheduler(r.c,()->r.time,()->r.elapsed,3600000,message->{});scheduler.tick();check(!r.c.autosave()&&!r.events.contains("cancel"),"no live overtaking and no cancellation");r.prepared();r.ack("SNAPSHOT_READY");r.ack("COMMITTED");r.prepared=new CompletableFuture<>();r.ready=new CompletableFuture<>();r.committed=new CompletableFuture<>();scheduler.tick();check(r.request.event().equals("hourly_save"),"queued hourly follows current work");});
  test("hourly queue does not cancel an already frozen live checkpoint",()->{var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,false,true);check(r.c.autosave(),"existing live");var scheduler=new HourlyScheduler(r.c,()->r.time,()->r.elapsed,3600000,message->{});scheduler.tick();check(r.frozen&&!r.events.contains("cancel")&&r.request.event().equals("periodic_save"),"let already running live finish");});
  test("startup pending hourly rejects new live before scheduler runs",()->{var f=new Fixture();check(!f.r.c.autosave()&&f.r.events.isEmpty(),"baseline priority starts at scheduler construction");f.tick();check(f.hourlyRequests()==1,"baseline retained");});
  test("prepare failure remains pending and retries with sixty-second backoff",()->{var f=new Fixture();f.tick();f.r.prepared.completeExceptionally(new Exception("unavailable"));f.tick();check(f.scheduler.pending()&&f.r.c.hourlySuccessGeneration()==0&&f.hourlyRequests()==1,"failure cannot consume due");f.renew();f.advance(59999);check(f.hourlyRequests()==1,"backoff preserved");f.advance(1);check(f.hourlyRequests()==2,"retry complete hourly without an autosave");f.success();check(!f.scheduler.pending(),"only matching commit consumes pending");});
  test("hourly save failure restores flags and remains pending",()->{var f=new Fixture();f.r.failSave=true;f.tick();f.r.prepared();f.tick();check(!f.r.frozen&&f.r.restores==1&&f.r.events.contains("cancel")&&f.scheduler.pending(),"failed forced save restores and preserves due");check(f.r.c.hourlyFailureGeneration()==1,"failure observable");});
  test("hourly barrier timeout restores and late completion cannot commit",()->{var f=new Fixture();f.r.barrier=new CompletableFuture<>();f.tick();f.r.prepared();f.advance(15000);f.r.barrier.complete(null);f.tick();check(!f.r.frozen&&f.r.restores==1&&!f.r.events.contains("snapshot")&&f.scheduler.pending(),"timeout retains pending and restores");});
  test("wrong commit identity never consumes hourly due",()->{var f=new Fixture();f.tick();f.r.prepared();f.r.ack("SNAPSHOT_READY");f.r.committed.complete(new Checkpoint.Reply(UUID.randomUUID(),"COMMITTED",f.r.time,f.r.snapshot));f.tick();check(f.scheduler.pending()&&f.r.c.hourlySuccessGeneration()==0&&!f.r.frozen,"matching commit required");});
  test("hourly forced save preserves a preexisting noSave true flag",()->{
   var r=new CheckpointTest.Rig();boolean[] flags={true,false};SavingGuard<Integer> guard=new SavingGuard<>(i->flags[i],(i,v)->flags[i]=v);
   Checkpoint.Port port=new Checkpoint.Port(){
    public CompletionStage<Checkpoint.Reply> prepare(Checkpoint.Request request){return r.prepare(request);}
    public CompletionStage<Checkpoint.Reply> snapshot(Checkpoint.Request request,long time){return r.snapshot(request,time);}
    public CompletionStage<Checkpoint.Reply> commit(Checkpoint.Request request,long time){return r.commit(request,time);}
    public CompletionStage<?> cancel(Checkpoint.Request request,long time){return r.cancel(request,time);}
    public boolean busy(){return false;}public void freeze(){guard.freeze(List.of(0,1));r.freeze();}
    public void flush(){check(flags[0]&&flags[1],"full forced flush runs with saving disabled in all dimensions");r.flush();}
    public CompletionStage<Void> barrier(){return r.barrier();}public void restore(){guard.restore();r.restore();}public void execute(Runnable action){action.run();}
   };
   r.c=new Checkpoint(port,()->r.time,()->r.elapsed,3600000,15000,true,true);var scheduler=new HourlyScheduler(r.c,()->r.time,()->r.elapsed,3600000,message->{});scheduler.tick();r.prepared();r.ack("SNAPSHOT_READY");r.ack("COMMITTED");scheduler.tick();check(flags[0]&&!flags[1]&&Collections.frequency(r.events,"flush")==1,"restore exact original flags without suppressing force save");
  });
  test("clock rollback cannot postpone hourly beyond elapsed boundary",()->{var f=new Fixture();f.r.time=3700000;f.tick();f.success();long delay=f.scheduler.nextDueWallMillis()-f.r.time;f.renew();f.r.time=100000;f.r.elapsed+=delay;f.tick();check(f.hourlyRequests()==2,"monotonic deadline survives backward wall clock");});
  test("forward jump coalesces missed hours into one current point",()->{var f=new Fixture();f.tick();f.success();f.renew();f.r.time+=5*3600000;f.r.elapsed+=1000;f.tick();check(f.hourlyRequests()==2,"one current point after jump");f.success();check(f.hourlyRequests()==2&&f.scheduler.nextDueWallMillis()>f.r.time,"no backlog replay");});
  test("shutdown prevents hourly retries and restores an active capture",()->{var f=new Fixture();f.tick();f.r.prepared();f.scheduler.shutdown();f.r.c.shutdown();f.renew();f.advance(86400000);check(!f.r.frozen&&f.hourlyRequests()==1,"stop scheduler and restore before shutdown");});
  test("FTB deferral retains hourly priority without freezing",()->{var f=new Fixture();f.r.busy=true;f.tick();check(f.scheduler.pending()&&f.r.events.isEmpty()&&!f.r.c.autosave(),"FTB busy leaves pending hourly");f.r.busy=false;f.advance(60000);check(f.hourlyRequests()==1,"retry after deferral");});
  test("hourly pending success and failure have status and generic logs",()->{var f=new Fixture();check(f.scheduler.status().contains("pending=true"),"initial status");f.tick();f.r.prepared.completeExceptionally(new Exception("HTTP unavailable"));f.tick();check(f.logs.stream().anyMatch(line->line.contains("failed")),"failure log");f.renew();f.advance(60000);f.success();check(f.logs.stream().anyMatch(line->line.contains("committed"))&&f.scheduler.status().contains("pending=false"),"success status/log");});
  test("custom short interval uses UTC minute boundaries",()->{var r=new CheckpointTest.Rig();r.c=new Checkpoint(r,()->r.time,()->r.elapsed,3600000,15000,true,true);var scheduler=new HourlyScheduler(r.c,()->r.time,()->r.elapsed,60000,message->{});scheduler.tick();r.prepared();r.ack("SNAPSHOT_READY");r.ack("COMMITTED");scheduler.tick();check(scheduler.nextDueWallMillis()==120000,"configured60s interval");});
  test("UTC boundary uses actual commit time rather than delayed scheduler poll",()->{var f=new Fixture();f.r.time=3599990;f.tick();f.r.prepared();f.r.ack("SNAPSHOT_READY");f.r.time+=9;f.r.elapsed+=9;f.r.ack("COMMITTED");f.renew();f.r.time+=2;f.r.elapsed+=2;f.tick();check(f.hourlyRequests()==2,"crossed boundary after commit is not silently skipped");});
  test("hourly interval configuration bounds",()->{var r=new CheckpointTest.Rig();for(long invalid:new long[]{-1,0,59999,31536000001L}){try{new HourlyScheduler(r.c,()->r.time,()->r.elapsed,invalid,message->{});throw new AssertionError("invalid interval accepted");}catch(IllegalArgumentException expected){}}new HourlyScheduler(r.c,()->r.time,()->r.elapsed,31536000000L,message->{});});
  System.out.println(passed+" independent hourly full-save checks passed");
 }
}
