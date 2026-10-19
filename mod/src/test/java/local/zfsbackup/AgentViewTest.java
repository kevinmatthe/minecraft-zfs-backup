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
  List<Map<String,Object>> points=new ArrayList<>();
  for(int i=0;i<12;i++)points.add(Map.of("name",String.format("mc-live-20261019T%02d0000Z",i),"event","periodic_save","guid","live"+i));
  for(int i=0;i<24;i++)points.add(Map.of("name",String.format("mc-hourly-20261019T%02d0000Z",i),"event","periodic_save","guid","hourly"+i));
  points.add(Map.of("name","pending-mc-live-20261019T010000Z"));points.add(Map.of("name","foreign-snapshot"));
  String dual=AgentView.snapshots(Map.of("snapshots",points));
  if(!dual.contains("mc-live-20261019T110000Z")||!dual.contains("mc-hourly-20261019T230000Z")||dual.contains("pending-")||dual.contains("foreign-snapshot"))throw new AssertionError("both local TTL families visible beyond old24 cap, pending and foreign excluded");
  System.out.println("5 business command rendering checks passed");
 }
}
