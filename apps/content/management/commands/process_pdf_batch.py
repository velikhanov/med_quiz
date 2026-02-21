from django.core.management.base import BaseCommand
from apps.content.services import background_worker


class Command(BaseCommand):
    help = 'Runs the PDF parsing worker in a detached background process.'

    def add_arguments(self, parser):
        # Accepts multiple PDF IDs and a batch size
        parser.add_argument('pdf_ids', nargs='+', type=int)
        parser.add_argument('--batch_size', type=int, default=5)

    def handle(self, *args, **options):
        pdf_ids = options['pdf_ids']
        batch_size = options['batch_size']

        self.stdout.write(f"Starting detached batch for PDFs: {pdf_ids} (Size: {batch_size})")
        background_worker(pdf_ids, batch_size)
        self.stdout.write("Detached batch completed.")
