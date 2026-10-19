package local.zfsbackup;
import java.util.UUID;
import java.util.concurrent.CompletionStage;
import java.util.function.LongSupplier;
/** Server-thread-only lifecycle; neither transport nor game implementation leaks into scheduling. */
public final class Checkpoint {
 public record Heartbeat(long updated, boolean ready) {}
 public record Response(UUID id,String phase,long updated,String snapshot) {}
 public record Request(UUID id,String phase,long created,long deadline,long changed) {}
 public interface Port {
  Heartbeat heartbeat() throws Exception;
  Response response() throws Exception;
  void publish(Request request) throws Exception;
  boolean busy() throws Exception;
  void freeze() throws Exception;
  void flush() throws Exception;
  CompletionStage<Void> barrier() throws Exception;
  void restore();
  void execute(Runnable action);
 }
 private enum State { IDLE, BARRIER, FROZEN, COMMIT, STOPPED }
 private final Port port;
 private final LongSupplier clock,elapsed;
 private final long interval, timeout;
 private final boolean onlyIfPlayersBeenOnline;
 private long activityGeneration,successfulActivityGeneration,checkpointActivityGeneration;
 private State state=State.IDLE;
 private long due,startedElapsed;
 private Request request;
 private boolean savingCaptured;
 private volatile boolean barrierDone;
 private volatile Throwable barrierError;
 private String detail="waiting for first interval";
 public Checkpoint(Port p,LongSupplier clock,long interval,long timeout){this(p,clock,clock,interval,timeout,true);}
 public Checkpoint(Port p,LongSupplier clock,LongSupplier elapsed,long interval,long timeout){this(p,clock,elapsed,interval,timeout,true);}
 public Checkpoint(Port p,LongSupplier clock,LongSupplier elapsed,long interval,long timeout,boolean onlyIfPlayersBeenOnline) {
  if(interval<60000||timeout<1000||timeout>15000)throw new IllegalArgumentException("interval >= 60s; freeze bound 1..15s");
  this.port=p;this.clock=clock;this.elapsed=elapsed;this.interval=interval;this.timeout=timeout;
  this.onlyIfPlayersBeenOnline=onlyIfPlayersBeenOnline;due=elapsed.getAsLong()+interval;
 }
 /** Called on the server thread for online player activity; in-flight generations stay dirty. */
 public void playerActivity(){if(state!=State.STOPPED)activityGeneration++;}
 public void tick() {
  if(state==State.STOPPED)return;
  long now=clock.getAsLong();
  if(state==State.IDLE){
   if(elapsed.getAsLong()>=due){
    if(onlyIfPlayersBeenOnline&&activityGeneration==successfulActivityGeneration){
     due=elapsed.getAsLong()+interval;detail="skipped automatic checkpoint: no player activity since last success";
    }else now();
   }
   return;
  }
  if(expired()){fail("checkpoint timed out");return;}
  try{
   if(state==State.BARRIER){
    if(!barrierDone)return;
    if(barrierError!=null){fail("asynchronous save failed: "+barrierError);return;}
    port.publish(request);state=State.FROZEN;detail="waiting for SNAPSHOT_READY";
   }
   Response response=port.response();
   now=clock.getAsLong();
   if(expired()){fail("checkpoint timed out during transport");return;}
   if(response==null||!response.id().equals(request.id()))return;
   if(response.updated()<request.created()||response.updated()>now+1000)return;
   if(response.phase().equals("ERROR")){fail("host reported ERROR");return;}
   if(state==State.FROZEN&&response.phase().equals("SNAPSHOT_READY")){
    restore();
    request=new Request(request.id(),"COMMIT",request.created(),request.deadline(),now);
    port.publish(request);state=State.COMMIT;detail="waiting for COMMITTED";
   }else if(state==State.COMMIT&&response.phase().equals("COMMITTED")){
    successfulActivityGeneration=checkpointActivityGeneration;
    detail="last success: "+response.snapshot();state=State.IDLE;due=elapsed.getAsLong()+interval;request=null;
   }
  }catch(Exception e){fail("checkpoint failed: "+e);}
 }
 public boolean now() {
  long now=clock.getAsLong();
  if(state!=State.IDLE)return false;
  // Manual requests may bypass the normal interval, but never the failure backoff.
  if(detail.startsWith("retry:")&&elapsed.getAsLong()<due)return false;
  try{
   Heartbeat heartbeat=port.heartbeat();
   if(port.busy()){defer(clock.getAsLong(),"FTB backup active");return false;}
   now=clock.getAsLong();
   if(heartbeat==null||!heartbeat.ready()||heartbeat.updated()<now-5000||heartbeat.updated()>now+1000){defer(now,"host heartbeat unavailable or stale");return false;}
   checkpointActivityGeneration=activityGeneration;
   startedElapsed=elapsed.getAsLong();
   request=new Request(UUID.randomUUID(),"FROZEN",now,now+timeout,0);
   state=State.BARRIER;barrierDone=false;barrierError=null;
   savingCaptured=true;port.freeze();port.flush();
   if(expired()){fail("forced flush exceeded deadline");return true;}
   UUID active=request.id();
   port.barrier().whenComplete((ignored,error)->port.execute(()->{
    if(request!=null&&request.id().equals(active)&&state==State.BARRIER){barrierError=error;barrierDone=true;}
   }));
   detail="waiting for save barrier";
  }catch(Exception e){if(request!=null)fail("save failed: "+e);else {defer(now,"readiness check failed: "+e);return false;}}
  return true;
 }
 private boolean expired(){return clock.getAsLong()>=request.deadline()||elapsed.getAsLong()-startedElapsed>=timeout;}
 private void defer(long now,String message){due=elapsed.getAsLong()+60000;detail="retry: "+message;}
 private void restore(){if(savingCaptured){port.restore();savingCaptured=false;}}
 private void fail(String message){
  restore();
  if(request!=null)try{port.publish(new Request(request.id(),"CANCEL",request.created(),request.deadline(),clock.getAsLong()));}catch(Exception ignored){}
  request=null;state=State.IDLE;defer(clock.getAsLong(),message);
 }
 public void shutdown(){if(state!=State.STOPPED&&request!=null)fail("server stopping");restore();state=State.STOPPED;}
 public String status(){return state+"; "+detail+"; activity_pending="+(activityGeneration!=successfulActivityGeneration)+"; retry/interval remaining ms="+Math.max(0,due-elapsed.getAsLong());}
}
