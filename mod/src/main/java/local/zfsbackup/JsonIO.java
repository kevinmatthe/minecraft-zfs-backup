package local.zfsbackup;

import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import com.google.gson.stream.JsonWriter;
import java.io.*;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.util.*;

/** Strict bounded JSON; duplicate keys and type coercion are never accepted. */
final class JsonIO {
 static final int MAX_BYTES=16384;
 static Map<String,Object> object(byte[] bytes)throws IOException{
  if(bytes.length>MAX_BYTES)throw new IOException("agent response exceeds 16 KiB");
  String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
  try(JsonReader reader=new JsonReader(new StringReader(text))){
   reader.setLenient(false);Object value=read(reader,0);
   if(reader.peek()!=JsonToken.END_DOCUMENT||!(value instanceof Map))throw new IOException("JSON object required");
   @SuppressWarnings("unchecked") Map<String,Object> result=(Map<String,Object>)value;return result;
  }catch(IllegalStateException|NumberFormatException error){throw new IOException("invalid agent JSON",error);}
 }
 private static Object read(JsonReader reader,int depth)throws IOException{
  if(depth>16)throw new IOException("JSON nesting exceeds limit");
  return switch(reader.peek()){
   case BEGIN_OBJECT->{
    Map<String,Object> map=new LinkedHashMap<>();reader.beginObject();
    while(reader.hasNext()){String key=reader.nextName();if(map.containsKey(key))throw new IOException("duplicate JSON field");map.put(key,read(reader,depth+1));}
    reader.endObject();yield map;
   }
   case BEGIN_ARRAY->{List<Object> list=new ArrayList<>();reader.beginArray();while(reader.hasNext())list.add(read(reader,depth+1));reader.endArray();yield list;}
   case STRING->reader.nextString();
   case BOOLEAN->reader.nextBoolean();
   case NULL->{reader.nextNull();yield null;}
   case NUMBER->{String number=reader.nextString();yield number.matches("-?(0|[1-9][0-9]*)")?Long.valueOf(number):new BigDecimal(number);}
   default->throw new IOException("invalid JSON value");
  };
 }
 static byte[] encode(Map<String,?> value)throws IOException{
  StringWriter text=new StringWriter();try(JsonWriter writer=new JsonWriter(text)){write(writer,value);}
  byte[] bytes=text.toString().getBytes(StandardCharsets.UTF_8);if(bytes.length>MAX_BYTES)throw new IOException("request exceeds limit");return bytes;
 }
 private static void write(JsonWriter writer,Object value)throws IOException{
  if(value==null)writer.nullValue();
  else if(value instanceof String string)writer.value(string);
  else if(value instanceof Boolean bool)writer.value(bool);
  else if(value instanceof Number number)writer.value(number);
  else if(value instanceof Map<?,?> map){writer.beginObject();for(var entry:map.entrySet()){if(!(entry.getKey() instanceof String key))throw new IOException("string JSON key required");writer.name(key);write(writer,entry.getValue());}writer.endObject();}
  else if(value instanceof Iterable<?> list){writer.beginArray();for(Object element:list)write(writer,element);writer.endArray();}
  else throw new IOException("unsupported JSON value");
 }
 static String string(Map<String,Object> map,String field)throws IOException{
  if(!(map.get(field) instanceof String value)||value.isEmpty())throw new IOException("missing string field "+field);return value;
 }
 static long integer(Map<String,Object> map,String field)throws IOException{
  if(!(map.get(field) instanceof Long value)||value<0)throw new IOException("missing integer field "+field);return value;
 }
 static UUID uuid(Map<String,Object> map,String field)throws IOException{
  String text=string(map,field);try{UUID id=UUID.fromString(text);if(!id.toString().equals(text))throw new IOException("noncanonical UUID");return id;}
  catch(IllegalArgumentException error){throw new IOException("invalid UUID");}
 }
}
