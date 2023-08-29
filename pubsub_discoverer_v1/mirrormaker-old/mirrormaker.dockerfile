# Base image
FROM python:3.8.12-alpine3.14

RUN apk -U upgrade
#RUN apk update && apk add bash wget openjdk11 --repository=http://dl-cdn.alpinelinux.org/alpine/edge/community
RUN apk update && apk add bash wget
RUN apk --no-cache add openjdk11-jdk openjdk11-jmods
RUN apk add gcc git libffi-dev g++ make

RUN apk add cyrus-sasl cyrus-sasl-crammd5 cyrus-sasl-digestmd5 cyrus-sasl-login cyrus-sasl-ntlm postfix
RUN apk add cyrus-sasl openldap-dev snappy-dev

RUN apk add gcc git libressl-dev g++ make && \
  cd /tmp && git clone https://github.com/edenhill/librdkafka.git && \
  cd librdkafka && git checkout tags/v2.0.2 && \
  ./configure --enable-ssl --enable-gssapi && make && make install && \
  #./configure && make && make install && \
  cd ../ && rm -rf librdkafka

# Set environment variables
ENV KAFKA_VERSION=3.3.1 \
    SCALA_VERSION=2.12 \
    JAVA_HOME=/usr \
    PATH=$PATH:$JAVA_HOME


# Install Kafka
RUN wget https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz && \
    tar -xzf kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz && \
    mv kafka_${SCALA_VERSION}-${KAFKA_VERSION} /opt/kafka && \
    rm kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz

RUN apk add build-base

COPY ./requirements.txt /requirements.txt
COPY ./TCSO_root_CA /usr/local/share/ca-certificates/tcso-root.crt

WORKDIR /

RUN python3.8 -m pip install setuptools

RUN python3.8 -m pip install -r requirements.txt  --extra-index-url https://gitlab.tinaa.teluslabs.net/api/v4/projects/1029/packages/pypi/simple --trusted-host gitlab.tinaa.teluslabs.net

ENV REQUESTS_CA_BUNDLE /usr/local/share/ca-certificates/tcso-root.crt
ENV CLASSPATH=mmchangetopic-1.0-SNAPSHOT.jar
ENV KAFKA_OPTS="-Dlog4j.configuration=file:/opt/kafka/config/connect-log4j.properties" 

COPY . /

# Set entrypoint
ENTRYPOINT ["python3.8", "main.py"]
