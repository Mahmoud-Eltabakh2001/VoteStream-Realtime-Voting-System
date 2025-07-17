import random
import time
from datetime import datetime
import os
import psycopg2
import simplejson as json
from confluent_kafka import Consumer, KafkaException, KafkaError, Producer
import uuid

from main import delivery_report

conf = {
    'bootstrap.servers': os.getenv("KAFKA_BROKER", "ed-kafka:29092"),
}

consumer = Consumer(conf | {
    'group.id': 'voting-group',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': False
})

producer = Producer(conf)

def consume_messages():
    result = []
    consumer.subscribe(['candidates_topic'])
    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    print(msg.error())
                    break
            else:
                result.append(json.loads(msg.value().decode('utf-8')))
                if len(result) == 3:
                    return result
    except KafkaException as e:
        print(e)

if __name__ == "__main__":
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        dbname=os.getenv("DB_NAME", "voting"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "postgres")
    )
    cur = conn.cursor()

    # Load candidates from DB
    cur.execute("""SELECT row_to_json(t) FROM (SELECT * FROM candidates) t;""")
    candidates = [row[0] for row in cur.fetchall()]
    if not candidates:
        raise Exception("No candidates found in database")
    else:
        print(candidates)

    consumer.subscribe(['voters_topic'])
    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            elif msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    print(msg.error())
                    break
            else:
                try:
                    voter = json.loads(msg.value().decode('utf-8'))
                    chosen_candidate = random.choice(candidates)
                    vote = voter | chosen_candidate | {
                        "vote_id": str(uuid.uuid4()),
                        "voting_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        "vote": 1
                    }

                    print(f"User {vote['voter_id']} is voting for candidate: {vote['candidate_id']}")

                    cur.execute("""
                        INSERT INTO votes (voter_id, candidate_id, voting_time, vote)
                        VALUES (%s, %s, %s, %s)
                    """, (
                        vote['voter_id'],
                        vote['candidate_id'],
                        vote['voting_time'],
                        vote['vote']
                    ))

                    conn.commit()

                    producer.produce(
                        'votes_topic',
                        key=vote["voter_id"],
                        value=json.dumps(vote),
                        on_delivery=delivery_report
                    )
                    producer.poll(0)

                except Exception as e:
                    print("Error:", e)
                    conn.rollback()  
                    continue
            time.sleep(0.2)
    except KafkaException as e:
        print("Kafka Error:", e)
