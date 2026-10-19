package local.zfsbackup;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
public final class SaveBarriersTest {
 public static void main(String[] args){
  Queue<Runnable> server=new ArrayDeque<>();CompletableFuture<Void> companion=new CompletableFuture<>(),nativeDone=new CompletableFuture<>();AtomicInteger submissions=new AtomicInteger();
  CompletionStage<Void> result=SaveBarriers.after(companion,server::add,()->{submissions.incrementAndGet();return nativeDone;});
  check(submissions.get()==0&&!result.toCompletableFuture().isDone(),"no native marker before companion barrier");companion.complete(null);check(submissions.get()==0&&server.size()==1,"marshal native submission onto server thread");server.remove().run();check(submissions.get()==1&&!result.toCompletableFuture().isDone(),"native stages remain asynchronous");nativeDone.complete(null);result.toCompletableFuture().join();
  CompletionStage<Void> failed=SaveBarriers.after(CompletableFuture.failedFuture(new Exception("companion")),server::add,()->{throw new AssertionError("must not submit");});check(failed.toCompletableFuture().isCompletedExceptionally(),"companion failure propagates without native submission");
  System.out.println("4 server-thread native barrier ordering checks passed");
 }
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
}
