package local.zfsbackup;

import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.neoforged.fml.common.Mod;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.RegisterCommandsEvent;
import net.neoforged.neoforge.event.server.ServerStartedEvent;
import net.neoforged.neoforge.event.server.ServerStoppingEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.io.*;
import java.nio.file.*;
import java.util.Properties;
import java.util.concurrent.*;

/** Dedicated-server adapter: game state is touched only by event/command callbacks and server.execute. */
@Mod(value="local_zfs_backup",dist=Dist.DEDICATED_SERVER)
public final class ZfsBackupMod {
 private static final Logger LOG=LoggerFactory.getLogger("ZfsBackup");
 private Checkpoint checkpoint;
 public ZfsBackupMod(){
  NeoForge.EVENT_BUS.addListener(this::started);
  NeoForge.EVENT_BUS.addListener(this::tick);
  NeoForge.EVENT_BUS.addListener(this::stopping);
  NeoForge.EVENT_BUS.addListener(this::commands);
 }
 private void started(ServerStartedEvent event){
  MinecraftServer server=event.getServer();
  try{
   Path root=server.getServerDirectory().toAbsolutePath().normalize();
   Properties properties=new Properties();Path config=root.resolve("config/local-zfs-backup.properties");
   if(Files.exists(config))try(Reader reader=Files.newBufferedReader(config)){properties.load(reader);}
   long interval=Long.parseLong(properties.getProperty("interval_seconds","3600"));
   long timeout=Long.parseLong(properties.getProperty("freeze_timeout_seconds","15"));
   if(interval<60||interval>31536000||timeout<1||timeout>15)throw new IllegalArgumentException("interval_seconds must be 60..31536000 and freeze_timeout_seconds 1..15");
   Path relative=Path.of(properties.getProperty("bridge_directory",".zfs-snapshot-bridge"));
   Path bridge=root.resolve(relative).normalize();
   if(relative.isAbsolute()||!bridge.startsWith(root)||bridge.equals(root))throw new IllegalArgumentException("bridge_directory must be below the server root");
   checkpoint=new Checkpoint(new ServerPort(server,new Mailbox(bridge)),System::currentTimeMillis,()->System.nanoTime()/1_000_000,interval*1000,timeout*1000);
   LOG.info("ZFS checkpoint bridge enabled: interval {}s, maximum freeze {}s",interval,timeout);
  }catch(Exception e){LOG.error("ZFS bridge disabled: invalid configuration",e);}
 }
 private void tick(ServerTickEvent.Post event){if(checkpoint!=null)checkpoint.tick();}
 private void stopping(ServerStoppingEvent event){if(checkpoint!=null){checkpoint.shutdown();checkpoint=null;}}
 private void commands(RegisterCommandsEvent event){
  event.getDispatcher().register(Commands.literal("zfsbackup").requires(source->source.hasPermission(4))
   .then(Commands.literal("now").executes(context->{
    boolean accepted=checkpoint!=null&&checkpoint.now();
    context.getSource().sendSuccess(()->Component.literal(accepted?"ZFS checkpoint started; use /zfsbackup status to inspect progress.":"Checkpoint deferred or unavailable. "+status()),false);
    return accepted?1:0;
   }))
   .then(Commands.literal("status").executes(context->{context.getSource().sendSuccess(()->Component.literal(status()),false);return 1;})));
 }
 private String status(){return checkpoint==null?"ZFS bridge unavailable (check server log/configuration)":checkpoint.status();}
 private static final class ServerPort implements Checkpoint.Port {
  private final MinecraftServer server;private final Mailbox mailbox;
  private final SavingGuard<ServerLevel> guard=new SavingGuard<>(level->level.noSave,(level,value)->level.noSave=value);
  ServerPort(MinecraftServer server,Mailbox mailbox){this.server=server;this.mailbox=mailbox;}
  public Checkpoint.Heartbeat heartbeat()throws Exception{return mailbox.heartbeat();}
  public Checkpoint.Response response()throws Exception{return mailbox.response();}
  public void publish(Checkpoint.Request request)throws Exception{mailbox.publish(request);}
  public boolean busy()throws Exception{
   try{return (boolean)Class.forName("net.creeperhost.ftbbackups.BackupHandler").getMethod("isRunning").invoke(null);}
   catch(ClassNotFoundException absent){return false;}
  }
  public void freeze(){guard.freeze(server.getAllLevels());}
  public void flush(){if(!server.saveEverything(false,true,true))throw new IllegalStateException("Minecraft forced save returned false");}
  public CompletionStage<Void> barrier()throws Exception{
   try{
    Object result=Class.forName("local.asyncplayersave.AsyncPlayerSave").getMethod("backupCheckpoint",MinecraftServer.class).invoke(null,server);
    if(!(result instanceof CompletionStage<?> stage))throw new IllegalStateException("AsyncPlayerSave backupCheckpoint did not return CompletionStage");
    return stage.thenApply(ignored->null);
   }catch(ClassNotFoundException absent){return CompletableFuture.completedFuture(null);}
  }
  public void restore(){guard.restore();}
  public void execute(Runnable action){server.execute(action);}
 }
}
