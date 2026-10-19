package local.zfsbackup;
import java.util.UUID;
import java.util.concurrent.CompletionStage;
import java.util.function.LongSupplier;
public final class Checkpoint {
 public record Request(UUID id,String event,long created,long deadline) {}
 public record Reply(UUID id,String state,long updated,String snapshot) {}
 public interface Port {
  CompletionStage<Reply> prepare(Request request);
  CompletionStage<Reply> snapshot(Request request,long frozen);
  CompletionStage<Reply> commit(Request request,long committed);
  CompletionStage<?> cancel(Request request,long cancelled);
  boolean busy() throws Exception;
  void freeze() throws Exception;
  void flush() throws Exception;
  CompletionStage<Void> barrier() throws Exception;
  void restore();
  void execute(Runnable action);
 }
 private enum State { IDLE, PREPARING, BARRIER, SNAPSHOTTING, COMMITTING, STOPPED }
 private final Port port;
 private final LongSupplier clock,elapsed;
 private final long interval,timeout;
 private final boolean onlyIfActive,autosaveDriven;
 private State state=State.IDLE;
 private long due,startedElapsed,commitStartedElapsed,activityGeneration,successfulGeneration,capturedGeneration;
 private long hourlySuccesses,hourlyFailures,hourlySuccessWall,hourlySuccessElapsed;
 private boolean hourlyQueued;
 private Request request;
 private boolean savingCaptured,retrying,reuseSavedBatch;
 private String detail="waiting for first interval",snapshot;

 public Checkpoint(Port port,LongSupplier clock,LongSupplier elapsed,long interval,long timeout,boolean onlyIfActive){
  this(port,clock,elapsed,interval,timeout,onlyIfActive,false);
 }
 public Checkpoint(Port port,LongSupplier clock,LongSupplier elapsed,long interval,long timeout,boolean onlyIfActive,boolean autosaveDriven){
  if(interval<60000||timeout<1000||timeout>15000)throw new IllegalArgumentException("interval >=60s; freeze budget 1..15s");
  this.port=port;this.clock=clock;this.elapsed=elapsed;this.interval=interval;this.timeout=timeout;this.onlyIfActive=onlyIfActive;this.autosaveDriven=autosaveDriven;
  if(autosaveDriven)detail="waiting for completed vanilla autosave";
  due=elapsed.getAsLong()+interval;
 }
 public void tick(){
  if(state==State.STOPPED)return;
  if(state!=State.IDLE){if(expired())fail("checkpoint deadline expired");return;}
  if(autosaveDriven)return;
  if(elapsed.getAsLong()<due)return;
  // Compatibility timer mode also represents a complete hourly save, never a live capture.
  hourly();
 }
 public boolean now(){return begin("manual_save",false);}
 /** Independent complete save, bypassing the live player-activity filter. */
 public boolean hourly(){return begin("hourly_save",false);}
 public void hourlyPending(boolean pending){hourlyQueued=pending;}
 public long hourlySuccessGeneration(){return hourlySuccesses;}
 public long hourlyFailureGeneration(){return hourlyFailures;}
 public long hourlySuccessWallMillis(){return hourlySuccessWall;}
 public long hourlySuccessElapsedMillis(){return hourlySuccessElapsed;}
 /** Called once on the server thread after a successful complete vanilla save batch. */
 public boolean autosave(){
  if(!autosaveDriven||hourlyQueued)return false;
  if(onlyIfActive&&!dirty()){detail="skipped automatic checkpoint: no player activity since last success";return false;}
  return begin("periodic_save",true);
 }
 /** An independent forced save must run after cancelling our frozen capture. */
 public void externalSave(){if(savingCaptured)fail("another world save started");}
 private boolean begin(String event,boolean savedBatch){
  if(state!=State.IDLE||(retrying&&elapsed.getAsLong()<due))return false;
  try{
   if(port.busy()){defer("FTB backup active");return false;}
   long created=clock.getAsLong();startedElapsed=elapsed.getAsLong();capturedGeneration=activityGeneration;
   request=new Request(UUID.randomUUID(),event,created,created+timeout);
   reuseSavedBatch=savedBatch;state=State.PREPARING;detail="waiting for agent PREPARED";
   // An autosave has already captured its data. Prevent new periodic writes while
   // HTTP preparation runs, without serializing the world a second time.
   if(savedBatch){savingCaptured=true;port.freeze();}
   await(port.prepare(request),State.PREPARING,"PREPARED",reply->prepared());
   return true;
  }catch(Exception error){if(request!=null)fail("prepare failed");else defer("readiness check failed");return false;}
 }
 private void prepared(){
  try{
   if(port.busy()){fail("FTB backup became active");return;}
   if(expired()){fail("prepare deadline expired before freezing");return;}
   state=State.BARRIER;detail="waiting for asynchronous save barrier";
   if(!reuseSavedBatch){savingCaptured=true;port.freeze();port.flush();}
   if(expired()){fail("forced flush exceeded deadline");return;}
   UUID id=request.id();
   port.barrier().whenComplete((ignored,error)->port.execute(()->{
    if(!active(id,State.BARRIER))return;
    if(error!=null){fail("asynchronous save barrier failed");return;}
    if(expired()){fail("save barrier exceeded deadline");return;}
    try{
     state=State.SNAPSHOTTING;detail="waiting for SNAPSHOT_READY";
     await(port.snapshot(request,clock.getAsLong()),State.SNAPSHOTTING,"SNAPSHOT_READY",this::snapshotReady);
    }catch(Exception failure){fail("snapshot request failed");}
   }));
  }catch(Exception error){fail("forced save failed");}
 }
 private void snapshotReady(Reply reply){
  snapshot=reply.snapshot();restore();
  try{
   if(expired()){fail("snapshot response exceeded deadline");return;}
   commitStartedElapsed=elapsed.getAsLong();state=State.COMMITTING;detail="waiting for COMMITTED";
   await(port.commit(request,clock.getAsLong()),State.COMMITTING,"COMMITTED",committed->{
    if(!snapshot.equals(committed.snapshot())){fail("committed snapshot identity changed");return;}
    if(request.event().equals("hourly_save")){
     hourlySuccesses++;hourlySuccessWall=clock.getAsLong();hourlySuccessElapsed=elapsed.getAsLong();
    }
    successfulGeneration=capturedGeneration;request=null;state=State.IDLE;retrying=false;
    due=elapsed.getAsLong()+interval;detail="last success: "+snapshot;
   });
  }catch(Exception error){fail("commit request failed");}
 }
 @FunctionalInterface private interface Success {void accept(Reply reply);}
 private void await(CompletionStage<Reply> operation,State expected,String expectedReply,Success success){
  UUID id=request.id();
  operation.whenComplete((reply,error)->port.execute(()->{
   if(!active(id,expected))return;
   if(expired()){fail("HTTP response exceeded deadline");return;}
   if(error!=null){fail("agent "+expectedReply+" request failed");return;}
   if(reply==null||!id.equals(reply.id())||!expectedReply.equals(reply.state())||reply.updated()<request.created()||reply.updated()>clock.getAsLong()+1000){
    fail("invalid or mismatching "+expectedReply+" response");return;
   }
   if(!expectedReply.equals("PREPARED")&&(reply.snapshot()==null||reply.snapshot().isBlank()||reply.snapshot().length()>200)){
    fail("missing snapshot identity");return;
   }
   try{success.accept(reply);}catch(Exception failure){fail("checkpoint transition failed");}
  }));
 }
 private boolean active(UUID id,State expected){return state==expected&&request!=null&&id.equals(request.id());}
 private boolean expired(){
  if(request==null)return false;
  // Saving has already resumed during COMMIT: bound acknowledgement separately.
  if(state==State.COMMITTING)return clock.getAsLong()>request.deadline()+60000||elapsed.getAsLong()-commitStartedElapsed>=timeout;
  return clock.getAsLong()>=request.deadline()||elapsed.getAsLong()-startedElapsed>=timeout;
 }
 private void restore(){if(savingCaptured){port.restore();savingCaptured=false;}}
 private void defer(String reason){due=elapsed.getAsLong()+60000;retrying=true;detail="retry: "+reason;}
 private void fail(String reason){
  restore();Request cancelled=request;
  if(cancelled!=null&&cancelled.event().equals("hourly_save"))hourlyFailures++;
  request=null;state=State.IDLE;defer(reason);
  if(cancelled!=null)try{port.cancel(cancelled,clock.getAsLong());}catch(Exception ignored){}
 }
 public void playerActivity(){if(state!=State.STOPPED)activityGeneration++;}
 private boolean dirty(){return activityGeneration!=successfulGeneration;}
 public boolean wantsShutdownSnapshot(boolean enabled){return enabled&&(!onlyIfActive||dirty());}
 public void shutdown(){
  if(state==State.STOPPED)return;
  if(request!=null)fail("server stopping");
  restore();state=State.STOPPED;
 }
 public String status(){return state+"; "+detail+"; hourly_pending="+hourlyQueued+"; activity_pending="+dirty()+"; "+(autosaveDriven?"automatic_source=vanilla_autosave":"retry/interval remaining ms="+Math.max(0,due-elapsed.getAsLong()));}
}
