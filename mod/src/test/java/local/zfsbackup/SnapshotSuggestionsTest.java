package local.zfsbackup;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.*;
import com.mojang.brigadier.suggestion.*;
import com.sun.net.httpserver.HttpServer;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

/** Real matching Brigadier dispatcher, real authenticated loopback HTTP, no Minecraft startup. */
public final class SnapshotSuggestionsTest {
 static int passed;
 static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);}
 static List<String> names(Suggestions suggestions){return suggestions.getList().stream().map(Suggestion::getText).toList();}
 static CompletableFuture<Suggestions> complete(CommandDispatcher<Boolean> dispatcher,String command,boolean operator){return dispatcher.getCompletionSuggestions(dispatcher.parse(command,operator));}
 static CommandDispatcher<Boolean> dispatcher(SnapshotSuggestions source){
  CommandDispatcher<Boolean> dispatcher=new CommandDispatcher<>();
  dispatcher.register(LiteralArgumentBuilder.<Boolean>literal("zfsbackup").requires(Boolean::booleanValue)
   .then(LiteralArgumentBuilder.<Boolean>literal("rollback")
    .then(LiteralArgumentBuilder.<Boolean>literal("status").executes(context->1))
    .then(RequiredArgumentBuilder.<Boolean,String>argument("snapshot",StringArgumentType.word()).suggests((context,builder)->source==null?Suggestions.empty():source.suggest(builder)).executes(context->1))));
  return dispatcher;
 }
 public static void main(String[] args)throws Exception{
  Path directory=Files.createTempDirectory("zfs-completion-test-");Path token=directory.resolve("token");Files.writeString(token,"test-only-completion-"+"a".repeat(40));
  String first="mc-hourly-20261019T060000Z",second="mc-hourly-20261019T070000Z",newest="mc-hourly-20261020T080000Z";
  AtomicReference<String> reply=new AtomicReference<>(new String(JsonIO.encode(Map.of("snapshots",List.of(Map.of("name",first),Map.of("name",second),Map.of("name","pending-"+first),Map.of("name","unmanaged-point")))),StandardCharsets.UTF_8));
  AtomicInteger hits=new AtomicInteger(),status=new AtomicInteger(200),delay=new AtomicInteger();AtomicBoolean mutated=new AtomicBoolean();AtomicLong elapsed=new AtomicLong(1000);
  HttpServer server=HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(),0),0);
  ExecutorService workers=Executors.newFixedThreadPool(2,task->{Thread thread=new Thread(task,"completion-http-test");thread.setDaemon(true);return thread;});server.setExecutor(workers);
  server.createContext("/",exchange->{
   hits.incrementAndGet();if(!exchange.getRequestMethod().equals("GET")||!exchange.getRequestURI().getPath().equals("/v1/snapshots"))mutated.set(true);
   String response=reply.get();int code=status.get(),wait=delay.get();
   try{if(wait>0)Thread.sleep(wait);byte[] bytes=response.getBytes(StandardCharsets.UTF_8);exchange.sendResponseHeaders(code,bytes.length);exchange.getResponseBody().write(bytes);}
   catch(InterruptedException interrupted){Thread.currentThread().interrupt();}catch(java.io.IOException disconnected){}finally{exchange.close();}
  });server.start();
  try(AgentClient client=new AgentClient(URI.create("http://127.0.0.1:"+server.getAddress().getPort()),token,Duration.ofMillis(600))){
   SnapshotSuggestions source=new SnapshotSuggestions(()->client.get("/v1/snapshots"),elapsed::get);CommandDispatcher<Boolean> dispatcher=dispatcher(source);
   List<String> all=names(complete(dispatcher,"zfsbackup rollback ",true).get(3,TimeUnit.SECONDS));
   check(all.containsAll(List.of("status",first,second))&&!all.contains("pending-"+first)&&!all.contains("unmanaged-point"),"trailing-space completion must include current published snapshots and status only");passed++;
   List<String> prefix=names(complete(dispatcher,"zfsbackup rollback mc-hourly-20261019T07",true).get(3,TimeUnit.SECONDS));
   check(prefix.equals(List.of(second)),"prefix completion");check(hits.get()==1,"brief cache avoids per-keypress requests");passed++;
   check(names(complete(dispatcher,"zfsbackup rollback ",false).get(3,TimeUnit.SECONDS)).isEmpty(),"non-operator receives no snapshot suggestions");passed++;
   reply.set(new String(JsonIO.encode(Map.of("snapshots",List.of(Map.of("name",newest)))),StandardCharsets.UTF_8));elapsed.addAndGet(501);
   List<String> refreshed=names(complete(dispatcher,"zfsbackup rollback mc-hourly-",true).get(3,TimeUnit.SECONDS));check(refreshed.equals(List.of(newest)),"on-demand refresh replaces removed snapshot names");passed++;
   elapsed.addAndGet(501);delay.set(350);long start=System.nanoTime();var a=complete(dispatcher,"zfsbackup rollback mc-",true);var b=complete(dispatcher,"zfsbackup rollback mc-hourly-20261020",true);
   check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start)<200&&!a.isDone()&&!b.isDone(),"completion must never wait for HTTP on dispatcher thread");
   check(names(a.get(3,TimeUnit.SECONDS)).equals(List.of(newest))&&names(b.get(3,TimeUnit.SECONDS)).equals(List.of(newest)),"coalesced queries retain their own prefixes");check(hits.get()==3,"concurrent completions share one HTTP query");passed++;
   delay.set(0);elapsed.addAndGet(501);status.set(503);
   check(names(complete(dispatcher,"zfsbackup rollback mc-",true).get(3,TimeUnit.SECONDS)).isEmpty(),"HTTP failure must produce successful empty suggestions");passed++;
   elapsed.addAndGet(501);status.set(200);reply.set("invalid json");
   check(names(complete(dispatcher,"zfsbackup rollback mc-",true).get(3,TimeUnit.SECONDS)).isEmpty(),"malformed HTTP response must complete empty");passed++;
   elapsed.addAndGet(501);reply.set("{\"snapshots\":[]}");delay.set(1200);
   check(names(complete(dispatcher,"zfsbackup rollback mc-",true).get(3,TimeUnit.SECONDS)).isEmpty(),"timed-out completion must resolve empty");passed++;
   check(names(complete(dispatcher(null),"zfsbackup rollback mc-",true).get(1,TimeUnit.SECONDS)).isEmpty(),"disabled bridge completion is empty");passed++;
   SnapshotSuggestions broken=new SnapshotSuggestions(()->{throw new IllegalStateException("submission failed");},elapsed::get);
   check(names(complete(dispatcher(broken),"zfsbackup rollback mc-",true).get(1,TimeUnit.SECONDS)).isEmpty(),"synchronous lookup exception cannot strand Brigadier future");passed++;
   SnapshotSuggestions negativeClock=new SnapshotSuggestions(()->CompletableFuture.completedFuture(Map.of("snapshots",List.of(Map.of("name",newest)))),()->-5000);
   check(names(complete(dispatcher(negativeClock),"zfsbackup rollback mc-",true).get(1,TimeUnit.SECONDS)).equals(List.of(newest)),"arbitrary negative monotonic clock origin cannot suppress initial lookup");passed++;
   check(!mutated.get(),"tab completion sends only read-only snapshot listing requests");passed++;
   int quiet=hits.get();Thread.sleep(200);check(hits.get()==quiet,"no background polling");passed++;
   System.out.println(passed+" real Brigadier/HTTP snapshot completion checks passed");
  }finally{server.stop(0);workers.shutdownNow();Files.delete(token);Files.delete(directory);}
 }
}
