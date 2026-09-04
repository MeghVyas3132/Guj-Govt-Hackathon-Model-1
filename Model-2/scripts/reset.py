import logging
from ml.config import settings
from ml.db import get_connection, commit
from ml.storage import _get_client
from minio.deleteobjects import DeleteObject

logging.basicConfig(level=logging.INFO)

def main():
    print("Connecting to MinIO...")
    client = _get_client()
    
    bucket = settings.minio_bucket
    if client.bucket_exists(bucket):
        objects = client.list_objects(bucket, recursive=True)
        delete_objs = [DeleteObject(obj.object_name) for obj in objects]
        if delete_objs:
            errors = client.remove_objects(bucket, delete_objs)
            err_count = sum(1 for _ in errors)
            print(f"Deleted {len(delete_objs) - err_count} objects from MinIO bucket '{bucket}'.")
        else:
            print(f"MinIO bucket '{bucket}' is already empty.")
    else:
        print(f"MinIO bucket '{bucket}' does not exist.")

    print("Connecting to PostgreSQL...")
    conn = get_connection()
    with conn.cursor() as cur:
        # Get counts before truncating
        cur.execute("SELECT COUNT(*) as count FROM detections;")
        det_count = cur.fetchone()['count']
        
        cur.execute("SELECT COUNT(*) as count FROM alerts;")
        alert_count = cur.fetchone()['count']
        
        cur.execute("TRUNCATE TABLE detections CASCADE;")
        cur.execute("TRUNCATE TABLE alerts CASCADE;")
        
        print(f"Truncated {det_count} detections and {alert_count} alerts from PostgreSQL.")
    
    commit(conn)
    conn.close()
    print("Reset complete.")

if __name__ == "__main__":
    main()
