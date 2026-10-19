package local.zfsbackup;

import java.util.function.*;

/** Server-thread scheduler for independent complete hourly saves, never live-point promotion. */
final class HourlyScheduler {
 private final Checkpoint checkpoint;
 private final LongSupplier clock,elapsed;
 private final long interval;
 private final Consumer<String> log;
 private long seenSuccess,seenFailure,dueWall,dueElapsed;
 private boolean pending=true,stopped;
 private String detail="startup baseline pending";
 HourlyScheduler(Checkpoint checkpoint,LongSupplier clock,LongSupplier elapsed,long interval,Consumer<String> log){
  if(interval<60000||interval>31536000000L)throw new IllegalArgumentException("hourly interval must be60..31536000 seconds");
  this.checkpoint=checkpoint;this.clock=clock;this.elapsed=elapsed;this.interval=interval;this.log=log;
  seenSuccess=checkpoint.hourlySuccessGeneration();seenFailure=checkpoint.hourlyFailureGeneration();
  checkpoint.hourlyPending(true);log.accept("Hourly full-save pending: startup baseline");
 }
 void tick(){
  if(stopped)return;
  if(checkpoint.hourlySuccessGeneration()!=seenSuccess){
   seenSuccess=checkpoint.hourlySuccessGeneration();
   long wall=checkpoint.hourlySuccessWallMillis(),delay=interval-Math.floorMod(wall,interval);
   dueWall=wall+delay;dueElapsed=checkpoint.hourlySuccessElapsedMillis()+delay;
   pending=false;detail="last hourly committed";
   checkpoint.hourlyPending(false);
   log.accept("Hourly full-save committed; next UTC boundary epoch ms="+dueWall);
  }
  if(checkpoint.hourlyFailureGeneration()!=seenFailure){
   seenFailure=checkpoint.hourlyFailureGeneration();detail="hourly failed; pending retry with checkpoint backoff";
   log.accept("Hourly full-save failed; remains pending for retry");
  }
  if(!pending&&(clock.getAsLong()>=dueWall||elapsed.getAsLong()>=dueElapsed)){
   pending=true;detail="UTC interval boundary pending";checkpoint.hourlyPending(true);
   log.accept("Hourly full-save pending: interval boundary");
  }
  // An occupied coordinator or its failure backoff is never bypassed or cancelled.
  if(pending&&checkpoint.hourly()){
   detail="hourly full save in progress";log.accept("Hourly full-save started");
  }
 }
 void shutdown(){stopped=true;checkpoint.hourlyPending(false);detail="hourly scheduler stopped";}
 boolean pending(){return pending;}
 long nextDueWallMillis(){return dueWall;}
 String status(){return "hourly_pending="+pending+"; hourly_successes="+seenSuccess+"; hourly_failures="+seenFailure+"; "+detail+"; next_hourly_epoch_ms="+(pending?"pending":Long.toString(dueWall));}
}
