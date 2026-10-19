package local.zfsbackup.mixin;
import local.zfsbackup.*;
import net.minecraft.world.level.chunk.storage.*;
import org.spongepowered.asm.mixin.*;
import org.spongepowered.asm.mixin.gen.Accessor;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import java.util.function.BooleanSupplier;
/** Skip only the dirty-writing superclass stage; PoiManager's other tick work still runs. */
@Mixin(SectionStorage.class)
public abstract class SectionStorageMixin implements NativeStorageAccess.Section {
 @Unique private WriteGate zfs$writeGate;
 @Accessor("simpleRegionStorage") public abstract SimpleRegionStorage zfs$sectionStorage();
 @Override public void zfs$setWriteGate(WriteGate gate){zfs$writeGate=gate;}
 @Inject(method="tick",at=@At("HEAD"),cancellable=true,require=1)
 private void zfs$deferDirtyWrites(BooleanSupplier time,CallbackInfo callback){if(zfs$writeGate!=null&&zfs$writeGate.suppressed())callback.cancel();}
}
