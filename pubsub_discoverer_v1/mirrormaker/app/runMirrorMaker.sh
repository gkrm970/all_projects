#!/bin/bash
#kafka-run-class.sh kafka.tools.MirrorMaker --consumer.config consumer.properties --producer.config producer.properties --num.streams 1 --whitelist='.*' --message.handler com.opencore.RenameTopicHandler --message.handler.args '{TopicName},meerkat.{TopicName}'
echo $1 $2 $3 $4 $5
#/opt/kafka/bin/kafka-run-class.sh kafka.tools.MirrorMaker $1 --consumer.config $2 --producer.config $3 --num.streams 1 --whitelist=$4 --message.handler com.opencore.RenameTopicHandler --message.handler.args "$4,$5"
#/opt/kafka/bin/connect-mirror-maker.sh $1
/opt/kafka/bin/connect-mirror-maker.sh $1