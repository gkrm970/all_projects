#To Run mirror maker 

kafka-run-class.sh kafka.tools.MirrorMaker connect-mirror-maker.properties --consumer.config consumer.properties --producer.config producer.properties --num.streams 1 --whitelist='SNMP' --message.handler com.opencore.RenameTopicHandler --message.handler.args 'SNMP,meerkat.SNMP'

#To Run source consumer command

kafka-console-consumer.sh --bootstrap-server 172.25.208.114:9092 --topic SNMP