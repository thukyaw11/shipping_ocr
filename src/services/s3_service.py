import boto3
from src.core.config import Config

_PUBLIC_BASE_URL = "https://pub-343cbead881747c79d3db832c7824527.r2.dev"


class S3Service:
    def __init__(self):
        self.s3 = boto3.client(
            service_name="s3",
            endpoint_url=Config.R2_ENDPOINT_URL,
            aws_access_key_id=Config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=Config.R2_SECRET_ACCESS_KEY,
            region_name="auto"
        )
        self.bucket = Config.R2_BUCKET_NAME

    def upload_file(self, file_obj, object_name: str, content_type: str):
        try:
            self.s3.upload_fileobj(
                file_obj,
                self.bucket,
                object_name,
                ExtraArgs={"ContentType": content_type}
            )
            return f"{_PUBLIC_BASE_URL}/{object_name}"
        except Exception as e:
            print(f"R2 Upload Error: {str(e)}")
            raise e


s3_service = S3Service()
