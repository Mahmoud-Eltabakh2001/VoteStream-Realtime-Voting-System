from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col
from pyspark.sql.functions import sum as _sum
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, TimestampType




if __name__ == "__main__":
    # Initialize SparkSession
    spark=(
        SparkSession
        .builder
        .appName("ElectionAnalysis")
        .config("spark.streaming.stopGracefullyOnShutdown",True)
        .config("spark.jars.packages","org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.0")
        .config("spark.jars", "/opt/spark/jars/postgresql-42.7.1.jar")
        .config("spark.sql.shuffle.partitions",5)
        .config("spark.sql.adaptive.enabled", "false")
        .master("local[*]")
        .getOrCreate()
     )


    # Define schemas for Kafka topics

    vote_schema = StructType([
        StructField("voter_id", StringType(), True),
        StructField("candidate_id", StringType(), True),
        StructField("voting_time", TimestampType(), True),
        StructField("voter_name", StringType(), True),
        StructField("party_affiliation", StringType(), True),
        StructField("biography", StringType(), True),
        StructField("campaign_platform", StringType(), True),
        StructField("photo_url", StringType(), True),
        StructField("candidate_name", StringType(), True),
        StructField("date_of_birth", StringType(), True),
        StructField("gender", StringType(), True),
        StructField("nationality", StringType(), True),
        StructField("registration_number", StringType(), True),
        StructField("address", StructType([
            StructField("street", StringType(), True),
            StructField("city", StringType(), True),
            StructField("state", StringType(), True),
            StructField("country", StringType(), True),
            StructField("postcode", StringType(), True)
        ]), True),
        StructField("email", StringType(), True),
        StructField("phone_number", StringType(), True),
        StructField("cell_number", StringType(), True),
        StructField("picture", StringType(), True),
        StructField("registered_age", IntegerType(), True),
        StructField("vote", IntegerType(), True)
    ])

    # Read data from Kafka 'votes_topic' and process it
    votes_df = spark.readStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", "ed-kafka:29092") \
        .option("subscribe", "votes_topic") \
        .option("failOnDataLoss", "false") \
        .option("startingOffsets", "earliest") \
        .load() \
        .selectExpr("CAST(value AS STRING)") \
        .select(from_json(col("value"), vote_schema).alias("data")) \
        .select("data.*")

    # Data preprocessing: type casting and watermarking
    votes_df = votes_df.withColumn("voting_time", col("voting_time").cast(TimestampType())) \
        .withColumn('vote', col('vote').cast(IntegerType()))
    enriched_votes_df = votes_df.withWatermark("voting_time", "1 minute")

    # Aggregate votes per candidate and turnout by location
    votes_per_candidate = enriched_votes_df.groupBy("candidate_id", "candidate_name", "party_affiliation",
                                                    "photo_url").agg(_sum("vote").alias("total_votes"))
    turnout_by_location = enriched_votes_df.groupBy("address.state").count().withColumnRenamed("count", "total_votes")

    # Write aggregated data to Kafka topics ('aggregated_votes_per_candidate', 'aggregated_turnout_by_location')
    votes_per_candidate_to_kafka = votes_per_candidate.selectExpr("to_json(struct(*)) AS value") \
        .writeStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", "ed-kafka:29092") \
        .option("topic", "aggregated_votes_per_candidate") \
        .option("checkpointLocation", "/app/checkpoints/checkpoint1") \
        .outputMode("update") \
        .start()

    def write_to_postgres_candidate(batch_df, batch_id):
        batch_df.write \
            .format("jdbc") \
            .option("url", "jdbc:postgresql://ed-postgres:5432/voting_db") \
            .option("dbtable", "votes_per_candidate") \
            .option("user", "postgres") \
            .option("password", "postgres") \
            .option("driver", "org.postgresql.Driver") \
            .mode("append") \
            .save()

    votes_per_candidate_to_postgres=votes_per_candidate \
        .writeStream \
        .outputMode("update") \
        .foreachBatch(write_to_postgres_candidate) \
        .option("checkpointLocation", "/app/checkpoints/postgres_votes_candidates") \
        .start()

    turnout_by_location_to_kafka = turnout_by_location.selectExpr("to_json(struct(*)) AS value") \
        .writeStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", "ed-kafka:29092") \
        .option("topic", "aggregated_turnout_by_location") \
        .option("checkpointLocation", "/app/checkpoints/checkpoint2") \
        .outputMode("update") \
        .start()

    def write_to_postgres_location(batch_df, batch_id):
        batch_df.write \
            .format("jdbc") \
            .option("url", "jdbc:postgresql://ed-postgres:5432/voting_db") \
            .option("dbtable", "turnout_by_location") \
            .option("user", "postgres") \
            .option("password", "postgres") \
            .option("driver", "org.postgresql.Driver") \
            .mode("append") \
            .save()

    turnout_by_location_to_postgres=turnout_by_location \
        .writeStream \
        .outputMode("update") \
        .foreachBatch(write_to_postgres_location) \
        .option("checkpointLocation", "/app/checkpoints/postgres_votes_locations") \
        .start()
    
     # Await termination for the streaming queries
    turnout_by_location_to_postgres.awaitTermination()
    votes_per_candidate_to_postgres.awaitTermination()
    votes_per_candidate_to_kafka.awaitTermination()
    turnout_by_location_to_kafka.awaitTermination()


