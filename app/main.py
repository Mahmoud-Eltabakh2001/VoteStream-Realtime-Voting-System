import random
import os
import time
import psycopg2
import requests
import simplejson as json
from confluent_kafka import Producer

BASE_URL = 'https://randomuser.me/api/?nat=gb'
PARTIES = ["Management Party", "Savior Party", "Tech Republic Party"]

random.seed(42)

def generate_voter_data():
    try:
        response = requests.get(BASE_URL, timeout=5)
        if response.status_code == 200:
            user_data = response.json()['results'][0]
            return {
                "voter_id": user_data['login']['uuid'],
                "voter_name": f"{user_data['name']['first']} {user_data['name']['last']}",
                "date_of_birth": user_data['dob']['date'],
                "gender": user_data['gender'],
                "nationality": user_data['nat'],
                "registration_number": user_data['login']['username'],
                "address": {
                    "street": f"{user_data['location']['street']['number']} {user_data['location']['street']['name']}",
                    "city": user_data['location']['city'],
                    "state": user_data['location']['state'],
                    "country": user_data['location']['country'],
                    "postcode": user_data['location']['postcode']
                },
                "email": user_data['email'],
                "phone_number": user_data['phone'],
                "cell_number": user_data['cell'],
                "picture": user_data['picture']['large'],
                "registered_age": user_data['registered']['age']
            }
    except Exception as e:
        print(f"[Error] Failed to fetch voter data: {e}")
        return None

def generate_candidate_data(candidate_number, total_parties):
    try:
        response = requests.get(BASE_URL + '&gender=' + ('female' if candidate_number % 2 == 1 else 'male'), timeout=5)
        if response.status_code == 200:
            user_data = response.json()['results'][0]
            return {
                "candidate_id": user_data['login']['uuid'],
                "candidate_name": f"{user_data['name']['first']} {user_data['name']['last']}",
                "party_affiliation": PARTIES[candidate_number % total_parties],
                "biography": "A brief bio of the candidate.",
                "campaign_platform": "Key campaign promises or platform.",
                "photo_url": user_data['picture']['large']
            }
    except Exception as e:
        print(f"[Error] Failed to fetch candidate data: {e}")
        return None

def delivery_report(err, msg):
    if err is not None:
        print(f'Message delivery failed: {err}')
    else:
        print(f'Message delivered to {msg.topic()} [{msg.partition()}]')

# Kafka Topics
voters_topic = 'voters_topic'

def create_tables(conn, cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id VARCHAR(255) PRIMARY KEY,
            candidate_name VARCHAR(255),
            party_affiliation VARCHAR(255),
            biography TEXT,
            campaign_platform TEXT,
            photo_url TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS voters (
            voter_id VARCHAR(255) PRIMARY KEY,
            voter_name VARCHAR(255),
            date_of_birth VARCHAR(255),
            gender VARCHAR(255),
            nationality VARCHAR(255),
            registration_number VARCHAR(255),
            address_street VARCHAR(255),
            address_city VARCHAR(255),
            address_state VARCHAR(255),
            address_country VARCHAR(255),
            address_postcode VARCHAR(255),
            email VARCHAR(255),
            phone_number VARCHAR(255),
            cell_number VARCHAR(255),
            picture TEXT,
            registered_age INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            voter_id VARCHAR(255) UNIQUE,
            candidate_id VARCHAR(255),
            voting_time TIMESTAMP,
            vote INT DEFAULT 1,
            PRIMARY KEY (voter_id, candidate_id)
        )
    """)
    conn.commit()

def insert_voters(conn, cur, voter):
    cur.execute("""
        INSERT INTO voters (
            voter_id, voter_name, date_of_birth, gender, nationality,
            registration_number, address_street, address_city, address_state,
            address_country, address_postcode, email, phone_number,
            cell_number, picture, registered_age
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (voter_id) DO NOTHING
    """, (
        voter["voter_id"], voter['voter_name'], voter['date_of_birth'], voter['gender'],
        voter['nationality'], voter['registration_number'], voter['address']['street'],
        voter['address']['city'], voter['address']['state'], voter['address']['country'],
        voter['address']['postcode'], voter['email'], voter['phone_number'],
        voter['cell_number'], voter['picture'], voter['registered_age']
    ))

    conn.commit()

if __name__ == "__main__":
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        dbname=os.getenv("DB_NAME", "voting"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASS", "postgres")
    )
    cur = conn.cursor()

    kafka_broker = os.getenv("KAFKA_BROKER", "ed-kafka:9092")
    producer = Producer({'bootstrap.servers': kafka_broker})
    print("Kafka producer initialized successfully.")
    
    create_tables(conn, cur)

    cur.execute("SELECT * FROM candidates")
    candidates = cur.fetchall()

    if len(candidates) == 0:
        for i in range(3):
            candidate = generate_candidate_data(i, 3)
            if candidate:
                cur.execute("""
                INSERT INTO candidates (candidate_id, candidate_name, party_affiliation, biography, campaign_platform, photo_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                candidate['candidate_id'], candidate['candidate_name'], candidate['party_affiliation'],
                candidate['biography'], candidate['campaign_platform'], candidate['photo_url']
                ))
                conn.commit()

    for i in range(1000):
        voter_data = generate_voter_data()
        if not voter_data:
            continue

        insert_voters(conn, cur, voter_data)

        try:
            producer.produce(
                voters_topic,
                key=voter_data["voter_id"],
                value=json.dumps(voter_data),
                callback=delivery_report
            )
            producer.poll(0)
            print(f'Produced voter {i}, data: {voter_data["voter_id"]}')
        except Exception as e:
            print(f"Kafka produce error: {e}")
        time.sleep(0.1)  
    producer.flush()
