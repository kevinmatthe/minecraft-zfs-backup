package local.zfsbackup.mixin;
import local.zfsbackup.*;
import net.minecraft.world.level.entity.*;
import org.spongepowered.asm.mixin.*;
import org.spongepowered.asm.mixin.gen.Accessor;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
/** Retain unload queue; manager.tick still processes pending loads and gameplay remains active. */
@Mixin(PersistentEntitySectionManager.class)
public abstract class EntitySectionManagerMixin implements NativeStorageAccess.Entities {
 @Unique private WriteGate zfs$writeGate;
 @Accessor("permanentStorage") public abstract EntityPersistentStorage<?> zfs$permanentStorage();
 @Override public void zfs$setWriteGate(WriteGate gate){zfs$writeGate=gate;}
 @Inject(method="processUnloads",at=@At("HEAD"),cancellable=true,require=1)
 private void zfs$deferUnloadWrites(CallbackInfo callback){if(zfs$writeGate!=null&&zfs$writeGate.suppressed())callback.cancel();}
}
