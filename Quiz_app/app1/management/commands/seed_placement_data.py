from django.core.management.base import BaseCommand

from app1.models import Category, Company, Quiz


COMPANIES = [
    ("TCS", "Tata Consultancy Services — India's largest IT services company."),
    ("Infosys", "Global leader in consulting, technology and outsourcing."),
    ("Wipro", "Leading technology services and consulting company."),
    ("Accenture", "Professional services with strong campus hiring."),
    ("Cognizant", "Multinational IT services and consulting."),
    ("Capgemini", "Global business and technology transformation partner."),
]

SECTIONS = ["Aptitude", "Reasoning", "English", "Coding", "Technical"]


class Command(BaseCommand):
    help = "Seed placement companies and test section categories."

    def handle(self, *args, **options):
        for name, desc in COMPANIES:
            Company.objects.get_or_create(name=name, defaults={"description": desc})

        for section in SECTIONS:
            Category.objects.get_or_create(name=section)

        self.stdout.write(self.style.SUCCESS("Placement companies and sections seeded."))
