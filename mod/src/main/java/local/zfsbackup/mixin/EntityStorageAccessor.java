package local.zfsbackup.mixin;
import net.minecraft.world.level.chunk.storage.*;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;
@Mixin(EntityStorage.class)
public interface EntityStorageAccessor {@Accessor("simpleRegionStorage") SimpleRegionStorage zfs$entityStorage();}
