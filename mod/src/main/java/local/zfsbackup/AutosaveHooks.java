package local.zfsbackup;
import net.minecraft.server.MinecraftServer;
/** Installed only for the active dedicated server; original native future failures stay observable. */
public final class AutosaveHooks {
 private static final SaveErrorLedger ERRORS=new SaveErrorLedger();
 private record Session(MinecraftServer server,AutosaveObserver observer){}
 private static volatile Session active;
 public static SaveErrorLedger errors(){return ERRORS;}
 public static void attach(MinecraftServer server,AutosaveObserver observer){active=new Session(server,observer);}
 public static void detach(MinecraftServer server){Session session=active;if(session!=null&&session.server()==server)active=null;}
 private static AutosaveObserver observer(MinecraftServer server){Session session=active;return session!=null&&session.server()==server?session.observer():null;}
 public static void tickStarted(MinecraftServer server){AutosaveObserver observer=observer(server);if(observer!=null)observer.tickStarted();}
 public static void beforeAutosave(MinecraftServer server){AutosaveObserver observer=observer(server);if(observer!=null)observer.beforeAutosave();}
 public static void afterAutosave(MinecraftServer server){AutosaveObserver observer=observer(server);if(observer!=null)observer.afterAutosave();}
 public static void fullSaveStarted(MinecraftServer server){AutosaveObserver observer=observer(server);if(observer!=null)observer.fullSaveStarted();}
 public static void fullSaveReturned(MinecraftServer server,boolean saved){AutosaveObserver observer=observer(server);if(observer!=null)observer.fullSaveReturned(saved);}
 public static void externalSaveStarted(MinecraftServer server){AutosaveObserver observer=observer(server);if(observer!=null)observer.externalSaveStarted();}
}
