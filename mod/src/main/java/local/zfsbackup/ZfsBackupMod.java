package local.zfsbackup;

import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.suggestion.Suggestions;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.EventPriority;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.server.ServerStoppedEvent;
import net.neoforged.neoforge.event.server.ServerStoppingEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Function;

/** Dedicated-server adapter. Running gameplay never waits for an HTTP response. */
@Mod(value="local_zfs_backup",dist=Dist.DEDICATED_SERVER)
public final class ZfsBackupMod {
 private static final Logger LOG=LoggerFactory.getLogger("ZfsBackup");
 private Checkpoint checkpoint;
 private HourlyScheduler hourlyScheduler;
 private AgentClient client;
 private ServerPort serverPort;
 private SnapshotSuggestions snapshotSuggestions;
 private boolean snapshotOnShutdown=true;

 public ZfsBackupMod(){
  NeoForge.EVENT_BUS.addListener(this::started);
  NeoForge.EVENT_BUS.addListener(this::tick);
  NeoForge.EVENT_BUS.addListener(this::stopping);
  // Other save integrations normally drain on NORMAL. The stopped event is after world close.
  NeoForge.EVENT_BUS.addListener(EventPriority.LOWEST,this::stopped);
  NeoForge.EVENT_BUS.addListener(this::commands);
  NeoForge.EVENT_BUS.addListener(this::loggedIn);
 }
 private void started(ServerStartedEvent event){
  MinecraftServer server=event.getServer();
  try{
   Path root=server.getServerDirectory().toAbsolutePath().normalize();
   Properties properties=new Properties();Path config=root.resolve("config/local-zfs-backup.properties");
   if(Files.exists(config))try(Reader reader=Files.newBufferedReader(config)){properties.load(reader);}
   // Natural live captures stay vanilla-driven; the full hourly schedule is independent.
   long interval=3600;
   long hourlyInterval=Long.parseLong(properties.getProperty("hourly_interval_seconds","3600"));
   if(hourlyInterval<60||hourlyInterval>31536000)throw new IllegalArgumentException("invalid hourly interval");
   long timeout=Long.parseLong(properties.getProperty("freeze_timeout_seconds","15"));
   if(timeout<1||timeout>15)throw new IllegalArgumentException("invalid freeze timeout");
   boolean onlyIfActive=bool(properties,"only_if_players_been_online",false);
   snapshotOnShutdown=bool(properties,"snapshot_on_shutdown",true);
   URI url=URI.create(properties.getProperty("agent_url","http://minecraft-zfs-agent:8765"));
   Path token=root.resolve(properties.getProperty("agent_token_file","config/local-zfs-backup.token")).normalize();
   client=new AgentClient(url,token);
   AgentClient activeClient=client;
   snapshotSuggestions=new SnapshotSuggestions(()->activeClient.get("/v1/snapshots"),()->System.nanoTime()/1_000_000);
   serverPort=new ServerPort(server,client);
   checkpoint=new Checkpoint(serverPort,System::currentTimeMillis,()->System.nanoTime()/1_000_000,interval*1000,timeout*1000,onlyIfActive,true);
   hourlyScheduler=new HourlyScheduler(checkpoint,System::currentTimeMillis,()->System.nanoTime()/1_000_000,hourlyInterval*1000,line->{
    if(line.contains("failed"))LOG.warn("{}",line);else LOG.info("{}",line);
   });
   AutosaveObserver observer=new AutosaveObserver(AutosaveHooks.errors(),baseline->{
    // Reconcile the independent due work before allowing a new live capture to take the slot.
    if(hourlyScheduler!=null)hourlyScheduler.tick();
    serverPort.errorBaseline=baseline;
    if(checkpoint!=null)checkpoint.autosave();
   },()->{if(checkpoint!=null)checkpoint.externalSave();});
   serverPort.observer=observer;AutosaveHooks.attach(server,observer);
   LOG.info("HTTP ZFS bridge enabled: vanilla autosave live captures and independent hourly full saves every {}s on UTC boundaries; checkpoint budget {}s, shutdown snapshot {}; legacy interval_seconds remains ignored",hourlyInterval,timeout,snapshotOnShutdown);
   client.lifecycle(AgentClient.LifecycleEvent.SERVER_STARTED,false,UUID.randomUUID(),System.currentTimeMillis()).whenComplete((reply,error)->{
    if(error==null)LOG.info("Server-started lifecycle event recorded");else LOG.warn("Server-started lifecycle event failed: {}",diagnostic(error));
   });
   client.get("/v1/rollbacks/current").whenComplete((job,error)->{
    if(error==null)LOG.info("Latest {}",AgentView.rollback(job));
   });
  }catch(Exception error){
   if(client!=null){client.close();client=null;}checkpoint=null;hourlyScheduler=null;serverPort=null;snapshotSuggestions=null;AutosaveHooks.detach(server);
   // Exceptions from URI/auth machinery may contain administrator values; never print them.
   LOG.error("HTTP ZFS bridge disabled: check agent URL, regular token file, configuration and native autosave/storage hooks");
  }
 }
 private static boolean bool(Properties properties,String key,boolean fallback){
  String value=properties.getProperty(key,Boolean.toString(fallback)).trim();
  if(!value.equals("true")&&!value.equals("false"))throw new IllegalArgumentException("invalid boolean configuration");
  return Boolean.parseBoolean(value);
 }
 private void loggedIn(PlayerEvent.PlayerLoggedInEvent event){if(checkpoint!=null)checkpoint.playerActivity();}
 private void tick(ServerTickEvent.Post event){
  if(checkpoint!=null){if(event.getServer().getPlayerCount()>0)checkpoint.playerActivity();if(hourlyScheduler!=null)hourlyScheduler.tick();checkpoint.tick();}
 }
 private void stopping(ServerStoppingEvent event){
  if(hourlyScheduler!=null)hourlyScheduler.shutdown();
  if(checkpoint!=null){
   if(event.getServer().getPlayerCount()>0)checkpoint.playerActivity();
   checkpoint.shutdown();
  }
  AutosaveHooks.detach(event.getServer());
 }
 private void stopped(ServerStoppedEvent event){
  if(client==null)return;
  try{
   // Verified in patched NeoForge21.1.251 MinecraftServer.runServer: stopServer() closes
   // ServerLevels and LevelStorageAccess before handleServerStopped posts this event.
   // This is the ONLY HTTP wait: gameplay/world saving has already stopped.
   boolean clean=event.getServer() instanceof ShutdownEvidenceAccess evidence&&evidence.zfs$hasCleanShutdown();
   boolean backup=clean&&checkpoint!=null&&checkpoint.wantsShutdownSnapshot(snapshotOnShutdown);
   if(!clean)LOG.warn("Shutdown save/close could not be verified; recording server-stopped event without requesting a snapshot");
   Map<String,Object> reply=client.lifecycle(AgentClient.LifecycleEvent.SERVER_STOPPED,backup,UUID.randomUUID(),System.currentTimeMillis()).toCompletableFuture().get(20,TimeUnit.SECONDS);
   LOG.info("Server-stopped lifecycle event accepted: {}",reply.get("state"));
  }catch(InterruptedException interrupted){Thread.currentThread().interrupt();LOG.warn("Server-stopped lifecycle wait interrupted");}
  catch(Exception error){LOG.warn("Server-stopped lifecycle request failed within bounded shutdown wait: {}",diagnostic(error));}
  finally{client.close();client=null;checkpoint=null;hourlyScheduler=null;serverPort=null;snapshotSuggestions=null;AutosaveHooks.detach(event.getServer());}
 }
 private void commands(RegisterCommandsEvent event){
  event.getDispatcher().register(Commands.literal("zfsbackup").requires(source->source.hasPermission(4))
   .then(Commands.literal("save").executes(context->save(context.getSource())))
   .then(Commands.literal("now").executes(context->save(context.getSource())))
   .then(Commands.literal("status").executes(context->{
    CommandSourceStack source=context.getSource();message(source,checkpoint==null?"HTTP ZFS bridge unavailable":checkpoint.status()+"; "+(hourlyScheduler==null?"hourly scheduler unavailable":hourlyScheduler.status()));
    return query(source,"/v1/status",AgentView::status);
   }))
   .then(Commands.literal("list").executes(context->query(context.getSource(),"/v1/snapshots",AgentView::snapshots)))
   .then(Commands.literal("rollback")
    .then(Commands.literal("status").executes(context->query(context.getSource(),"/v1/rollbacks/current",AgentView::rollback)))
    .then(Commands.argument("snapshot",StringArgumentType.word()).suggests((context,builder)->{
     SnapshotSuggestions suggestions=snapshotSuggestions;
     return !context.getSource().hasPermission(4)||suggestions==null?Suggestions.empty():suggestions.suggest(builder);
    }).executes(context->{
     CommandSourceStack source=context.getSource();
     if(client==null){message(source,"HTTP ZFS bridge unavailable");return 0;}
     String snapshot=StringArgumentType.getString(context,"snapshot");
     if(!SnapshotNames.isPublished(snapshot)){message(source,"Choose an exact managed snapshot name from /zfsbackup list.");return 0;}
     UUID id=UUID.randomUUID();
     message(source,"Rollback requested; the agent will stop this server before restoring the selected world.");
     // Capture the actual command executor on this server thread; identity is never a chat argument.
     Map<String,Object> actor;
     if(source.getEntity() instanceof ServerPlayer player){
      String name=player.getGameProfile().getName();
      actor=Map.of("kind","player","name",name==null||name.isBlank()?source.getTextName():name,"uuid",player.getUUID().toString());
     }else actor=Map.of("kind","console","name",source.getTextName());
     complete(source,client.rollback(id,snapshot,actor),AgentView::rollback);return 1;
    }))));
 }
 private int save(CommandSourceStack source){
  boolean accepted=checkpoint!=null&&checkpoint.now();
  message(source,accepted?"Checkpoint requested; preparing with the HTTP agent.":"Checkpoint deferred or bridge unavailable. "+(checkpoint==null?"Check server configuration/logs.":checkpoint.status()));
  return accepted?1:0;
 }
 private int query(CommandSourceStack source,String path,Function<Map<String,Object>,String> format){
  if(client==null){message(source,"HTTP ZFS bridge unavailable");return 0;}
  complete(source,client.get(path),format);return 1;
 }
 private void complete(CommandSourceStack source,CompletionStage<Map<String,Object>> result,Function<Map<String,Object>,String> format){
  MinecraftServer server=source.getServer();
  result.whenComplete((reply,error)->server.execute(()->{
   if(server.isStopped())return;
   message(source,error==null?format.apply(reply):diagnostic(error));
  }));
 }
 private static void message(CommandSourceStack source,String text){source.sendSuccess(()->Component.literal(text),false);}
 private static String diagnostic(Throwable error){
  for(Throwable cause=error;cause!=null;cause=cause.getCause()){
   String message=cause.getMessage();
   if(message!=null&&message.matches("agent returned HTTP [0-9]{3}"))return message;
  }
  return "agent unavailable, timed out or returned an invalid response";
 }
 private static final class ServerPort implements Checkpoint.Port {
  private final MinecraftServer server;private final AgentClient client;
  private final WriteGate writeGate=new WriteGate();
  private final NativeSaveBarrier nativeSave;
  private AutosaveObserver observer;
  private long errorBaseline;
  private final SavingGuard<ServerLevel> guard=new SavingGuard<>(level->level.noSave,(level,value)->level.noSave=value);
  ServerPort(MinecraftServer server,AgentClient client){this.server=server;this.client=client;this.nativeSave=new NativeSaveBarrier(server,writeGate);}
  public CompletionStage<Checkpoint.Reply> prepare(Checkpoint.Request request){return client.prepare(request);}
  public CompletionStage<Checkpoint.Reply> snapshot(Checkpoint.Request request,long frozen){return client.snapshot(request,frozen);}
  public CompletionStage<Checkpoint.Reply> commit(Checkpoint.Request request,long committed){return client.commit(request,committed);}
  public CompletionStage<?> cancel(Checkpoint.Request request,long cancelled){return client.cancel(request,cancelled);}
  public boolean busy()throws Exception{
   try{return (boolean)Class.forName("net.creeperhost.ftbbackups.BackupHandler").getMethod("isRunning").invoke(null);}
   catch(ClassNotFoundException absent){return false;}
  }
  public void freeze(){nativeSave.bind();writeGate.freeze();guard.freeze(server.getAllLevels());}
  public void flush()throws Exception{
   errorBaseline=AutosaveHooks.errors().generation();
   observer.ownCapture(()->{if(!server.saveEverything(false,true,true))throw new IllegalStateException("forced save failed");});
  }
  public CompletionStage<Void> barrier()throws Exception{
   CompletionStage<Void> companion;
   try{
    Object result=Class.forName("local.asyncplayersave.AsyncPlayerSave").getMethod("backupCheckpoint",MinecraftServer.class).invoke(null,server);
    if(!(result instanceof CompletionStage<?> stage))throw new IllegalStateException("incompatible asynchronous save integration");
    companion=stage.thenApply(ignored->null);
   }catch(ClassNotFoundException absent){companion=CompletableFuture.completedFuture(null);}
   long baseline=errorBaseline;
   return SaveBarriers.after(companion,server::execute,()->nativeSave.synchronize(baseline));
  }
  public void restore(){try{guard.restore();}finally{writeGate.restore();}}
  public void execute(Runnable action){server.execute(action);}
 }
}
