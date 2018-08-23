"""Kusto ingest client for Python."""

import base64
import random
import uuid
import os
import time
import tempfile

from azure.storage.common import CloudStorageAccount

from azure.kusto.data.request import KustoClient
from ._descriptors import BlobDescriptor, FileDescriptor
from ._ingestion_blob_info import _IngestionBlobInfo
from ._resource_manager import _ResourceManager


class KustoIngestClient(object):
    """Kusto ingest client for Python.
    KustoIngestClient works with both 2.x and 3.x flavors of Python.
    All primitive types are supported.

    Tests are run using pytest.
    """

    def __init__(self, kcsb):
        """Kusto Ingest Client constructor.
        :param kcsb: The connection string to initialize KustoClient.
        """
        kusto_client = KustoClient(kcsb)
        self._resource_manager = _ResourceManager(kusto_client)
    
    def ingest_from_dataframe(self, df, ingestion_properties):
        blobs = []

        file_name = 'df_{timestamp}_{pid}.csv.gz'.format(timestamp=int(time.time()), pid=os.getpid())
        temp_file_path = os.path.join(tempfile.gettempdir(), file_name)

        df.to_csv(temp_file_path, index=False, encoding='utf-8', header=False, compression='gzip')

        fd = FileDescriptor(temp_file_path)

        blob_name = "{db}__{table}__{guid}__{file}".format(
            db=ingestion_properties.database,
            table=ingestion_properties.table,
            guid=uuid.uuid4(),
            file=file_name,
        )

        containers = self._resource_manager.get_containers()
        container_details = random.choice(containers)
        storage_client = CloudStorageAccount(
            container_details.storage_account_name, sas_token=container_details.sas
        )
        blob_service = storage_client.create_block_blob_service()

        def prog_cb(current, total):
            print(current,total)    

        result = blob_service.create_blob_from_path(
            container_name=container_details.object_name,
            blob_name=blob_name,
            file_path=temp_file_path,
            progress_callback=prog_cb
        )                

        url = blob_service.make_blob_url(
            container_details.object_name,
            blob_name,
            sas_token=container_details.sas,
        )

        blobs.append(BlobDescriptor(url, fd.size))

        self.ingest_from_blobs(
            blobs,
            ingestion_properties=ingestion_properties
        )

        fd.delete_files()
        os.unlink(temp_file_path)

    def ingest_from_files(
        self, files, ingestion_properties
    ):
        """Enqueuing an ingest command from local files.
        :param files: List of FileDescriptor or file paths. The list of files to be ingested.
        :param azure.kusto.ingest.IngestionProperties ingestion_properties: Ingestion properties.
        """
        blobs = list()
        file_descriptors = list()
        containers = self._resource_manager.get_containers()

        for file in files:
            if isinstance(file, FileDescriptor):
                descriptor = file
            else:
                descriptor = FileDescriptor(file)

            file_descriptors.append(descriptor)
            blob_name = "{db}__{table}__{guid}__{file}".format(
                db=ingestion_properties.database,
                table=ingestion_properties.table,
                guid=uuid.uuid4(),
                file=descriptor.stream_name,
            )

            # TODO: maybe should only pick one for entire ingestion?
            container_details = random.choice(containers)
            storage_client = CloudStorageAccount(
                container_details.storage_account_name, sas_token=container_details.sas)
            blob_service = storage_client.create_block_blob_service()

            blob_service.create_blob_from_stream(
                container_name=container_details.object_name,
                blob_name=blob_name,
                stream=descriptor.zipped_stream,
            )
            url = blob_service.make_blob_url(
                container_details.object_name,
                blob_name,
                sas_token=container_details.sas,
            )
            blobs.append(BlobDescriptor(url, descriptor.size))

        self.ingest_from_blobs(
            blobs,
            ingestion_properties=ingestion_properties
        )

    def ingest_from_blobs(
        self, blobs, ingestion_properties
    ):
        """Enqueuing an ingest command from azure blobs.
        :param files: List of BlobDescriptor. The list of blobs to be ingested. Please provide the
            raw blob size to each of the descriptors.
        :param azure.kusto.ingest.IngestionProperties ingestion_properties: Ingestion properties.
        """
        queues = self._resource_manager.get_ingestion_queues()

        for blob in blobs:

            queue_details = random.choice(queues)
            storage_client = CloudStorageAccount(queue_details.storage_account_name, sas_token=queue_details.sas)
            queue_service = storage_client.create_queue_service()
            authorization_context = self._resource_manager.get_authorization_context()
            ingestion_blob_info = _IngestionBlobInfo(
                blob,
                ingestion_properties=ingestion_properties,
                auth_context=authorization_context
            )
            ingestion_blob_info_json = ingestion_blob_info.to_json()
            encoded = base64.b64encode(ingestion_blob_info_json.encode("utf-8")).decode(
                "utf-8"
            )
            queue_service.put_message(queue_name=queue_details.object_name, content=encoded)
