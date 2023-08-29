# set base image (host OS)
FROM tiangolo/uvicorn-gunicorn-fastapi:python3.11-slim

RUN apt-get update && apt-get -y upgrade
RUN apt-get -y install bash wget

RUN apt-get -y install openjdk-17-jdk
RUN apt-get -y install gcc git libffi-dev g++ make

#RUN apt-get update
#RUN apt-get -y install cyrus-sasl cyrus-sasl-crammd5 cyrus-sasl-digestmd5 cyrus-sasl-login cyrus-sasl-ntlm postfix
#RUN apt-get -y install cyrus-sasl openldap-dev snappy-dev

#RUN apk add cyrus-sasl cyrus-sasl-crammd5 cyrus-sasl-digestmd5 cyrus-sasl-login cyrus-sasl-ntlm postfix
#RUN apk add cyrus-sasl openldap-dev snappy-dev

RUN apt-get -y install gcc git libressl-dev g++ make; \
  cd /tmp && git clone https://github.com/edenhill/librdkafka.git; \
  cd librdkafka && git checkout tags/v2.0.2; \
  ./configure --enable-ssl --enable-gssapi && make && make install; \
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


WORKDIR /app
ENV PYTHONPATH=/app

COPY ./app/requirements.txt ./
COPY ./TCSO_root_CA /usr/local/share/ca-certificates/tcso-root.crt

RUN python3 -m pip install cython==0.29.36
RUN python3 -m pip install -r requirements.txt --no-build-isolation  --extra-index-url https://gitlab.tinaa.teluslabs.net/api/v4/projects/1029/packages/pypi/simple --trusted-host gitlab.tinaa.teluslabs.net
#RUN python3 -m pip install -r /requirements.txt

ENV REQUESTS_CA_BUNDLE /usr/local/share/ca-certificates/tcso-root.crt
ENV CLASSPATH=mmchangetopic-1.0-SNAPSHOT.jar
ENV KAFKA_OPTS="-Dlog4j.configuration=file:/opt/kafka/config/connect-log4j.properties"

COPY ./app /app

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8086"]
