FROM redis:7.0.11-alpine
RUN chmod 775 -R /data
CMD [ "redis-server", "*:6379"]