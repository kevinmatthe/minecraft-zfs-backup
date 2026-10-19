package local.zfsbackup;

import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

public final class AgentClientTest {
 static int passed;
 static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
 static void fails(CompletionStage<?> stage,String secret)throws Exception{
  try{stage.toCompletableFuture().get(3,TimeUnit.SECONDS);throw new AssertionError("request should fail closed");}
  catch(ExecutionException expected){check(!expected.toString().contains(secret),"error must not expose token");passed++;}
 }
 public static void main(String[] args)throws Exception{
  Path directory=Files.createTempDirectory("zfs-http-client-test-");Path tokenFile=directory.resolve("token");
  String token="test-only-"+"a".repeat(40);Files.writeString(tokenFile,token+"\n");
  UUID id=UUID.randomUUID();long now=System.currentTimeMillis();
  String prepared="{\"id\":\""+id+"\",\"state\":\"PREPARED\",\"updated_ms\":"+now+"}";
  AtomicReference<String> response=new AtomicReference<>(prepared),body=new AtomicReference<>(),path=new AtomicReference<>(),auth=new AtomicReference<>();
  AtomicInteger status=new AtomicInteger(200),delay=new AtomicInteger(),hits=new AtomicInteger();
  AtomicBoolean partial=new AtomicBoolean();
  HttpServer server=HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(),0),0);
  ExecutorService workers=Executors.newFixedThreadPool(2,r->{Thread t=new Thread(r,"mock-http");t.setDaemon(true);return t;});server.setExecutor(workers);
  server.createContext("/",exchange->{
   hits.incrementAndGet();path.set(exchange.getRequestURI().getPath());auth.set(exchange.getRequestHeaders().getFirst("Authorization"));
   body.set(new String(exchange.getRequestBody().readNBytes(16385),StandardCharsets.UTF_8));
   String reply=response.get();int code=status.get(),wait=delay.get();
   if(reply.equals("@lifecycle")){
    Map<String,Object> event=JsonIO.object(body.get().getBytes(StandardCharsets.UTF_8));
    reply=new String(JsonIO.encode(Map.of("id",event.get("id"),"event",event.get("event"),"state","SKIPPED","updated_ms",System.currentTimeMillis())),StandardCharsets.UTF_8);
   }
   try{if(wait>0)Thread.sleep(wait);byte[] bytes=reply.getBytes(StandardCharsets.UTF_8);exchange.getResponseHeaders().set("Location","/steal");exchange.sendResponseHeaders(code,bytes.length);if(partial.get()){exchange.getResponseBody().write(bytes,0,1);exchange.getResponseBody().flush();Thread.sleep(1400);exchange.getResponseBody().write(bytes,1,bytes.length-1);}else exchange.getResponseBody().write(bytes);}
   catch(InterruptedException e){Thread.currentThread().interrupt();}catch(java.io.IOException disconnected){}finally{exchange.close();}
  });server.start();URI uri=URI.create("http://127.0.0.1:"+server.getAddress().getPort());
  try(AgentClient client=new AgentClient(uri,tokenFile,Duration.ofMillis(800))){
   Checkpoint.Request request=new Checkpoint.Request(id,"manual_save",now,now+15000);
   Checkpoint.Reply reply=client.prepare(request).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(reply.id().equals(id)&&reply.state().equals("PREPARED"),"real HTTP prepare reply");
   check(path.get().equals("/v1/checkpoints")&&auth.get().equals("Bearer "+token),"authenticated endpoint");
   check(body.get().contains("\"event\":\"manual_save\"")&&body.get().contains("\"deadline_ms\":"+(now+15000)),"typed prepare body");passed++;
   UUID hourlyId=UUID.randomUUID();response.set(prepared.replace(id.toString(),hourlyId.toString()));
   client.prepare(new Checkpoint.Request(hourlyId,"hourly_save",now,now+15000)).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(body.get().contains("\"event\":\"hourly_save\"")&&path.get().equals("/v1/checkpoints"),"independent hourly full-save event sent over real HTTP");passed++;
   response.set(prepared.replace("PREPARED","SNAPSHOT_READY").replace("}",",\"snapshot\":\"mc-hourly-20261019T060000Z\"}"));
   check(client.snapshot(request,now+1).toCompletableFuture().get(3,TimeUnit.SECONDS).state().equals("SNAPSHOT_READY"),"snapshot response");
   check(path.get().endsWith("/"+id+"/snapshot")&&body.get().equals("{\"frozen_ms\":"+(now+1)+"}"),"snapshot endpoint/body");passed++;
   response.set(response.get().replace("SNAPSHOT_READY","COMMITTED"));client.commit(request,now+2).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(path.get().endsWith("/commit")&&body.get().equals("{\"committed_ms\":"+(now+2)+"}"),"commit endpoint/body");passed++;
   response.set("{}");client.cancel(request,now+3).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(path.get().endsWith("/cancel")&&body.get().equals("{\"cancelled_ms\":"+(now+3)+"}"),"cancel endpoint/body");passed++;
   for(String invalid:List.of("not json",prepared.replace("\"updated_ms\":"+now,"\"updated_ms\":\""+now+"\""),prepared.replace("\"state\":","\"id\":\""+id+"\",\"state\":"),prepared.replace(id.toString(),"1-1-1-1-1"),"{\"padding\":\""+"x".repeat(17000)+"\"}")){
    response.set(invalid);fails(client.prepare(request),token);
   }
   response.set("{\"error\":{\"code\":\"unauthorized\",\"message\":\""+token+"\"}}");status.set(401);fails(client.prepare(request),token);
   response.set(prepared);status.set(302);int before=hits.get();fails(client.prepare(request),token);check(hits.get()==before+1,"redirects cannot forward token");
   status.set(200);delay.set(1400);long begin=System.nanoTime();CompletionStage<?> pending=client.prepare(request);
   check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-begin)<500&&!pending.toCompletableFuture().isDone(),"HTTP must return asynchronously");fails(pending,token);delay.set(0);
   partial.set(true);fails(client.prepare(request),token);partial.set(false);
   fails(client.prepare(new Checkpoint.Request(UUID.randomUUID(),"manual_save",now-30000,now-1000)),token);
   fails(client.rollback(UUID.randomUUID(),"../arbitrary-path"),token);
   response.set(prepared.replace("PREPARED","COMMITTED").replace("}",",\"snapshot\":\"mc-hourly-20261019T060000Z\"}"));
   client.commit(new Checkpoint.Request(id,"manual_save",now-30000,now-1000),now-2000).toCompletableFuture().get(3,TimeUnit.SECONDS);passed++;
   response.set("@lifecycle");UUID eventId=UUID.randomUUID();
   client.lifecycle(AgentClient.LifecycleEvent.SERVER_STARTED,false,eventId,now).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(path.get().equals("/v1/lifecycle")&&body.get().contains("\"event\":\"server_started\"")&&body.get().contains("\"backup_requested\":false"),"typed startup event");passed++;
   client.lifecycle(AgentClient.LifecycleEvent.SERVER_STOPPED,true,UUID.randomUUID(),now).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(body.get().contains("\"event\":\"server_stopped\"")&&body.get().contains("\"backup_requested\":true"),"typed stopped event with backup request");passed++;
   UUID jobId=UUID.randomUUID();String snapshot="mc-hourly-20261019T060000Z";status.set(202);
   response.set(new String(JsonIO.encode(Map.of("id",jobId.toString(),"snapshot",snapshot,"phase","INTENT")),StandardCharsets.UTF_8));
   Map<String,Object> job=client.rollback(jobId,snapshot).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(path.get().equals("/v1/rollbacks")&&job.get("id").equals(jobId.toString()),"accepted rollback job");passed++;
   String live="mc-live-20261019T060000Z";UUID liveJob=UUID.randomUUID();
   response.set(new String(JsonIO.encode(Map.of("id",liveJob.toString(),"snapshot",live,"phase","INTENT")),StandardCharsets.UTF_8));
   check(client.rollback(liveJob,live).toCompletableFuture().get(3,TimeUnit.SECONDS).get("snapshot").equals(live),"live-family rollback accepted over real HTTP");passed++;
   UUID playerId=UUID.randomUUID();Map<String,Object> playerActor=Map.of("kind","player","name","TestOperator","uuid",playerId.toString());
   client.rollback(liveJob,live,playerActor).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(playerActor.equals(JsonIO.object(body.get().getBytes(StandardCharsets.UTF_8)).get("actor")),"real HTTP player rollback actor includes trusted name and canonical UUID");passed++;
   Map<String,Object> consoleActor=Map.of("kind","console","name","Server");
   client.rollback(liveJob,live,consoleActor).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(consoleActor.equals(JsonIO.object(body.get().getBytes(StandardCharsets.UTF_8)).get("actor")),"real HTTP console actor never invents player UUID or human identity");passed++;
   client.rollback(liveJob,live).toCompletableFuture().get(3,TimeUnit.SECONDS);
   check(!JsonIO.object(body.get().getBytes(StandardCharsets.UTF_8)).containsKey("actor"),"legacy rollback overload omits optional actor");passed++;
   for(String invalid:List.of("pending-"+live,"foreign-20261019T060000Z",live+"-extra","mc-live-20261019T060000z"))fails(client.rollback(UUID.randomUUID(),invalid),token);
   status.set(200);client.get("/v1/rollbacks/current").toCompletableFuture().get(3,TimeUnit.SECONDS);check(path.get().equals("/v1/rollbacks/current"),"job status on future startup");passed++;
   int quietHits=hits.get();Thread.sleep(250);check(hits.get()==quietHits,"client sends no periodic heartbeat or file polling requests");passed++;
   System.out.println(passed+" real HTTP client checks passed");
  }finally{server.stop(0);workers.shutdownNow();Files.delete(tokenFile);Files.delete(directory);}
 }
}
