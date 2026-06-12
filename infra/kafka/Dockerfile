FROM apache/kafka:4.3.1

COPY --chmod=0755 create-topics.sh /create-topics.sh

ENTRYPOINT ["/bin/bash", "/create-topics.sh"]
