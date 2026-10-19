package local.zfsbackup;
import net.minecraft.world.level.chunk.storage.*;
import net.minecraft.world.level.entity.*;
/** Runtime access/capability checks make absent core hooks fail closed before freezing. */
public interface NativeStorageAccess {
 interface ServerObserved {boolean zfs$autosaveObserved();}
 interface StoreObserved {boolean zfs$storeObserved();}
 interface Section {SimpleRegionStorage zfs$sectionStorage();void zfs$setWriteGate(WriteGate gate);}
 interface Entities {EntityPersistentStorage<?> zfs$permanentStorage();void zfs$setWriteGate(WriteGate gate);}
}
