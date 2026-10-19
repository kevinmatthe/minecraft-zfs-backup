package local.zfsbackup;

import java.io.*;
import java.net.URI;
import java.net.http.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.Flow;

/** Active asynchronous HTTP API. No heartbeat, mailbox, redirects or token-bearing diagnostics. */
final class AgentClient implements AutoCloseable {
 enum LifecycleEvent {
  SERVER_STARTED("server_started"),SERVER_STOPPED("server_stopped");
  final String wire;
  LifecycleEvent(String wire){this.wire=wire;}
 }
 private final URI url;
 private final String token;
 private final Duration requestTimeout;
 private final ExecutorService workers;
 private final HttpClient http;
 private final ScheduledThreadPoolExecutor timeouts;
 private final Semaphore inFlight=new Semaphore(8);
 AgentClient(URI url,Path tokenFile)throws IOException{this(url,tokenFile,Duration.ofSeconds(10));}
 AgentClient(URI url,Path tokenFile,Duration requestTimeout)throws IOException{
  if(!Set.of("http","https").contains(url.getScheme())||url.getHost()==null||url.getRawUserInfo()!=null||url.getRawQuery()!=null||url.getRawFragment()!=null||!(url.getPath().isEmpty()||url.getPath().equals("/")))throw new IOException("agent_url must be an HTTP(S) origin without credentials or path");
  if(requestTimeout.isNegative()||requestTimeout.isZero()||requestTimeout.compareTo(Duration.ofSeconds(30))>0)throw new IOException("invalid HTTP timeout");
  if(!Files.isRegularFile(tokenFile,LinkOption.NOFOLLOW_LINKS))throw new IOException("agent token must be a regular file");
  byte[] bytes;try(InputStream input=Files.newInputStream(tokenFile,LinkOption.NOFOLLOW_LINKS)){bytes=input.readNBytes(4097);}
  String secret=new String(bytes,StandardCharsets.UTF_8).strip();
  if(bytes.length>4096||secret.length()<32||!secret.matches("[!-~]+"))throw new IOException("agent token must contain at least 32 printable non-space ASCII characters");
  this.url=url;this.token=secret;this.requestTimeout=requestTimeout;
  AtomicInteger sequence=new AtomicInteger();
  workers=new ThreadPoolExecutor(4,4,0,TimeUnit.MILLISECONDS,new ArrayBlockingQueue<>(64),task->{Thread thread=new Thread(task,"zfs-agent-http-"+sequence.incrementAndGet());thread.setDaemon(true);return thread;},new ThreadPoolExecutor.AbortPolicy());
  timeouts=new ScheduledThreadPoolExecutor(1,task->{Thread thread=new Thread(task,"zfs-agent-http-timeout");thread.setDaemon(true);return thread;});
  timeouts.setRemoveOnCancelPolicy(true);
  http=HttpClient.newBuilder().executor(workers).connectTimeout(Duration.ofSeconds(3)).followRedirects(HttpClient.Redirect.NEVER).version(HttpClient.Version.HTTP_1_1).build();
 }
 CompletionStage<Checkpoint.Reply> prepare(Checkpoint.Request request){
  Map<String,Object> body=new LinkedHashMap<>();body.put("id",request.id().toString());body.put("event",request.event());body.put("created_ms",request.created());body.put("deadline_ms",request.deadline());
  return checkpoint("/v1/checkpoints",body,request);
 }
 CompletionStage<Checkpoint.Reply> snapshot(Checkpoint.Request request,long frozen){return checkpoint(path(request,"snapshot"),Map.of("frozen_ms",frozen),request);}
 CompletionStage<Checkpoint.Reply> commit(Checkpoint.Request request,long committed){
  if(committed<request.created()||committed>request.deadline())return CompletableFuture.failedFuture(new IOException("commit timestamp outside checkpoint bounds"));
  return reply(call("POST",path(request,"commit"),Map.of("committed_ms",committed),requestTimeout,0));
 }
 CompletionStage<?> cancel(Checkpoint.Request request,long cancelled){return call("POST",path(request,"cancel"),Map.of("cancelled_ms",cancelled),requestTimeout,0);}
 private static String path(Checkpoint.Request request,String action){return "/v1/checkpoints/"+request.id()+"/"+action;}
 private CompletionStage<Checkpoint.Reply> checkpoint(String path,Map<String,?> body,Checkpoint.Request request){
  long remaining=request.deadline()-System.currentTimeMillis();
  if(remaining<=0)return CompletableFuture.failedFuture(new IOException("checkpoint deadline expired"));
  Duration timeout=Duration.ofMillis(Math.min(remaining,requestTimeout.toMillis()));
  return reply(call("POST",path,body,timeout,0));
 }
 private CompletionStage<Checkpoint.Reply> reply(CompletionStage<Map<String,Object>> response){
  return response.thenApply(map->{
   try{return new Checkpoint.Reply(JsonIO.uuid(map,"id"),JsonIO.string(map,"state"),JsonIO.integer(map,"updated_ms"),map.get("snapshot") instanceof String name?name:null);}
   catch(IOException error){throw new CompletionException(error);}
  });
 }
 CompletionStage<Map<String,Object>> get(String path){return call("GET",path,null,requestTimeout,0);}
 CompletionStage<Map<String,Object>> rollback(UUID id,String snapshot){return rollback(id,snapshot,null);}
 CompletionStage<Map<String,Object>> rollback(UUID id,String snapshot,Map<String,Object> actor){
  if(!SnapshotNames.isPublished(snapshot))return CompletableFuture.failedFuture(new IOException("select a managed published snapshot name"));
  Map<String,Object> body=new LinkedHashMap<>();body.put("id",id.toString());body.put("snapshot",snapshot);
  if(actor!=null)body.put("actor",Map.copyOf(actor));
  return call("POST","/v1/rollbacks",body,requestTimeout,202).thenApply(job->{
   try{if(!JsonIO.uuid(job,"id").equals(id)||!JsonIO.string(job,"snapshot").equals(snapshot))throw new IOException("mismatching rollback job");return job;}
   catch(IOException error){throw new CompletionException(error);}
  });
 }
 CompletionStage<Map<String,Object>> lifecycle(LifecycleEvent event,boolean backup,UUID id,long created){
  Map<String,Object> body=new LinkedHashMap<>();body.put("id",id.toString());body.put("event",event.wire);body.put("created_ms",created);body.put("backup_requested",backup);
  return call("POST","/v1/lifecycle",body,Duration.ofSeconds(15),0).thenApply(map->{
   try{
    if(!JsonIO.uuid(map,"id").equals(id)||!JsonIO.string(map,"event").equals(event.wire)||!Set.of("RECORDED","COMMITTED","SKIPPED").contains(JsonIO.string(map,"state")))throw new IOException("mismatching lifecycle response");
    JsonIO.integer(map,"updated_ms");return map;
   }catch(IOException error){throw new CompletionException(error);}
  });
 }
 private CompletionStage<Map<String,Object>> call(String method,String path,Map<String,?> body,Duration timeout,int requiredStatus){
  if(!path.matches("/v1/[a-z0-9/-]+"))return CompletableFuture.failedFuture(new IOException("invalid API endpoint"));
  if(!inFlight.tryAcquire())return CompletableFuture.failedFuture(new IOException("agent request concurrency limit reached"));
  try{
   HttpRequest.Builder builder=HttpRequest.newBuilder(url.resolve(path)).timeout(timeout).header("Authorization","Bearer "+token).header("Accept","application/json");
   if(body==null)builder.GET();else builder.header("Content-Type","application/json").method(method,HttpRequest.BodyPublishers.ofByteArray(JsonIO.encode(body)));
   CompletableFuture<HttpResponse<byte[]>> wire=http.sendAsync(builder.build(),response->new BoundedBody());
   CompletableFuture<HttpResponse<byte[]>> bounded=new CompletableFuture<>();
   ScheduledFuture<?> expiry=timeouts.schedule(()->{
    if(bounded.completeExceptionally(new IOException("agent response deadline expired")))wire.cancel(true);
   },timeout.toMillis(),TimeUnit.MILLISECONDS);
   wire.whenComplete((reply,error)->{if(error==null)bounded.complete(reply);else bounded.completeExceptionally(new IOException("agent request failed"));});
   return bounded.whenComplete((reply,error)->expiry.cancel(false)).handle((response,error)->{
    if(error!=null)throw new CompletionException(new IOException("agent HTTP request failed"));
    if(response.statusCode()<200||response.statusCode()>=300||(requiredStatus!=0&&response.statusCode()!=requiredStatus))throw new CompletionException(new IOException("agent returned HTTP "+response.statusCode()));
    try{return JsonIO.object(response.body());}catch(IOException invalid){throw new CompletionException(invalid);}
   }).whenComplete((ignored,error)->inFlight.release());
  }catch(Exception error){inFlight.release();return CompletableFuture.failedFuture(new IOException("agent request could not be submitted"));}
 }
 public void close(){http.shutdownNow();workers.shutdownNow();timeouts.shutdownNow();}
 private static final class BoundedBody implements HttpResponse.BodySubscriber<byte[]> {
  private final CompletableFuture<byte[]> done=new CompletableFuture<>();
  private final ByteArrayOutputStream bytes=new ByteArrayOutputStream();
  private Flow.Subscription subscription;
  public CompletionStage<byte[]> getBody(){return done;}
  public void onSubscribe(Flow.Subscription subscription){this.subscription=subscription;subscription.request(1);}
  public void onNext(List<ByteBuffer> buffers){
   for(ByteBuffer buffer:buffers){
    if(buffer.remaining()>JsonIO.MAX_BYTES-bytes.size()){subscription.cancel();done.completeExceptionally(new IOException("agent response exceeds 16 KiB"));return;}
    byte[] chunk=new byte[buffer.remaining()];buffer.get(chunk);bytes.writeBytes(chunk);
   }
   subscription.request(1);
  }
  public void onError(Throwable error){done.completeExceptionally(new IOException("agent response failed"));}
  public void onComplete(){done.complete(bytes.toByteArray());}
 }
}
