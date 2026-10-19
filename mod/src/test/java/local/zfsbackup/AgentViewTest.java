package local.zfsbackup;
import java.util.*;
public final class AgentViewTest {
 public static void main(String[] args){
  Map<String,Object> state=Map.of("ready",true,"checkpoint",Map.of("state","PREPARED"),"last_success",Map.of("snapshot","mc-hourly-20261019T060000Z"));
  String status=AgentView.status(state);if(!status.contains("ready=true")||!status.contains("PREPARED")||!status.contains("mc-hourly-20261019T060000Z"))throw new AssertionError("readable typed status");
  String snapshots=AgentView.snapshots(Map.of("snapshots",List.of(Map.of("name","mc-hourly-20261019T060000Z","event","manual_save","guid","123"))));
  if(!snapshots.contains("manual_save")||!snapshots.contains("mc-hourly-20261019T060000Z"))throw new AssertionError("snapshot list");
  String rollback=AgentView.rollback(Map.of("id",UUID.randomUUID().toString(),"phase","STOPPING","snapshot","mc-hourly-20261019T060000Z","token","must-never-display"));
  if(!rollback.contains("STOPPING")||rollback.contains("must-never-display"))throw new AssertionError("only approved job fields");
  String unicode=AgentView.rollback(Map.of("state","🛑"));if(unicode.contains("🛑"))throw new AssertionError("untrusted command fields sanitized");
  System.out.println("4 business command rendering checks passed");
 }
}
