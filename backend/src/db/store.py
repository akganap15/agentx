"""
In-memory store for hackathon demo.

Implements the same interface as a real DB layer so agents and routes
can call store.get_business(), store.save_customer(), etc. without
knowing whether they're talking to Postgres or an in-memory dict.

Pre-populated with "Pete's Plumbing" demo data so the demo works
immediately after `uvicorn backend.server:app --reload`.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from backend.src.models.business import Business, BusinessHours, FAQ
from backend.src.models.conversation import Conversation, ConversationMessage, MessageRole
from backend.src.models.customer import Customer

logger = logging.getLogger(__name__)

DEMO_BUSINESS_ID = "demo-petes-plumbing"


class InMemoryStore:
    """
    Thread-safe* in-memory store.

    *FastAPI runs in an async event loop so there's no real thread contention;
    we use deepcopy on reads to simulate immutability.

    Interface mirrors what a SQLAlchemy async repository would expose:
      - get_business / save_business / list_businesses / delete_business
      - get_customer / save_customer / list_customers
      - get_conversations / save_conversation
    """

    def __init__(self) -> None:
        self._businesses: Dict[str, Business] = {}
        self._customers: Dict[str, Customer] = {}  # keyed by phone
        self._conversations: Dict[str, Conversation] = {}  # keyed by conv id

    # ------------------------------------------------------------------ #
    # Business operations
    # ------------------------------------------------------------------ #

    async def get_business(self, business_id: str) -> Optional[Business]:
        return deepcopy(self._businesses.get(business_id))

    async def save_business(self, business: Business) -> Business:
        business.updated_at = datetime.utcnow()
        self._businesses[business.id] = deepcopy(business)
        return business

    async def list_businesses(self) -> List[Business]:
        return [deepcopy(b) for b in self._businesses.values()]

    async def delete_business(self, business_id: str) -> None:
        self._businesses.pop(business_id, None)

    # ------------------------------------------------------------------ #
    # Customer operations
    # ------------------------------------------------------------------ #

    async def get_customer(self, phone: str) -> Optional[Customer]:
        return deepcopy(self._customers.get(phone))

    async def save_customer(self, customer: Customer) -> Customer:
        customer.updated_at = datetime.utcnow()
        self._customers[customer.phone] = deepcopy(customer)
        return customer

    async def list_customers(self, business_id: Optional[str] = None) -> List[Customer]:
        customers = list(self._customers.values())
        if business_id:
            customers = [c for c in customers if c.business_id == business_id]
        return [deepcopy(c) for c in customers]

    # ------------------------------------------------------------------ #
    # Conversation operations
    # ------------------------------------------------------------------ #

    async def get_conversations(self, business_id: str) -> List[Conversation]:
        return [
            deepcopy(c)
            for c in self._conversations.values()
            if c.business_id == business_id
        ]

    async def save_conversation(self, conv: Conversation) -> Conversation:
        conv.updated_at = datetime.utcnow()
        self._conversations[conv.id] = deepcopy(conv)
        return conv

    async def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        return deepcopy(self._conversations.get(conversation_id))


# ---------------------------------------------------------------------------
# Pre-populate the demo store
# ---------------------------------------------------------------------------

def _build_demo_store() -> InMemoryStore:
    """
    Creates and seeds the singleton demo store with Pete's Plumbing data.
    Called once at module import time.
    """
    store = InMemoryStore()

    # ---- Demo Business: Pete's Plumbing ----
    petes = Business(
        id=DEMO_BUSINESS_ID,
        name="Pete's Plumbing",
        industry="plumbing",
        owner_name="Pete",
        phone="+15551234567",
        email="pete@petesplumbing.example.com",
        website="https://petesplumbing.example.com",
        address="123 Main St, Austin, TX 78701",
        timezone="America/Chicago",
        sms_number="+15557654321",
        brand_voice="friendly, knowledgeable, and reassuring — like a neighbour who happens to be a master plumber",
        hours={
            "monday":    BusinessHours(open="08:00", close="18:00"),
            "tuesday":   BusinessHours(open="08:00", close="18:00"),
            "wednesday": BusinessHours(open="08:00", close="18:00"),
            "thursday":  BusinessHours(open="08:00", close="18:00"),
            "friday":    BusinessHours(open="08:00", close="17:00"),
            "saturday":  BusinessHours(open="09:00", close="14:00"),
            "sunday":    BusinessHours(closed=True),
        },
        faqs=[
            FAQ(
                question="How quickly can you come out for an emergency?",
                answer="We offer same-day emergency service. Call or text us and we'll have a technician to you within 2 hours.",
            ),
            FAQ(
                question="Do you offer free estimates?",
                answer="Yes! We provide free estimates for all non-emergency work. Text us your issue and we'll get you a quote.",
            ),
            FAQ(
                question="What areas do you service?",
                answer="We service all of Austin and surrounding areas including Round Rock, Cedar Park, and Pflugerville.",
            ),
            FAQ(
                question="Are you licensed and insured?",
                answer="Absolutely. Pete's Plumbing is fully licensed (TX Plumbing License #12345) and carries $2M liability insurance.",
            ),
            FAQ(
                question="What payment methods do you accept?",
                answer="We accept cash, all major credit cards, Venmo, and Zelle.",
            ),
        ],
        google_place_id="ChIJdemo1234567890",
        appointment_duration_minutes=90,
        booking_buffer_minutes=30,
        win_back_days=60,
        lead_capture_enabled=True,
        review_responses_enabled=True,
        after_hours_enabled=True,
        booking_enabled=True,
        campaigns_enabled=True,
    )

    # Use synchronous dict access for initial seeding (no await needed)
    store._businesses[petes.id] = petes

    # ---- Demo Customers ----
    now = datetime.utcnow()

    demo_customers = [
        Customer(
            id="cust-001",
            phone="+15550001001",
            name="Alice Johnson",
            email="alice@example.com",
            business_id=DEMO_BUSINESS_ID,
            is_lead=False,
            lead_stage="closed",
            total_visits=3,
            last_visit_at=now - timedelta(days=45),
            first_contact_at=now - timedelta(days=180),
            last_contact_at=now - timedelta(days=45),
            opted_in_sms=True,
            notes="Regular customer. Had water heater replaced and two drain clogs fixed.",
        ),
        Customer(
            id="cust-002",
            phone="+15550001002",
            name="Bob Martinez",
            email="bob@example.com",
            business_id=DEMO_BUSINESS_ID,
            is_lead=True,
            lead_stage="qualified",
            total_visits=0,
            first_contact_at=now - timedelta(days=2),
            last_contact_at=now - timedelta(days=1),
            opted_in_sms=True,
            notes="Interested in bathroom renovation plumbing. Budget ~$3k. Wants appointment this week.",
        ),
        Customer(
            id="cust-003",
            phone="+15550001003",
            name="Carol White",
            business_id=DEMO_BUSINESS_ID,
            is_lead=False,
            lead_stage="closed",
            total_visits=1,
            last_visit_at=now - timedelta(days=95),
            first_contact_at=now - timedelta(days=95),
            last_contact_at=now - timedelta(days=95),
            opted_in_sms=True,
            notes="Had kitchen sink repaired. Has not returned — win-back candidate.",
            no_show_count=1,
        ),
        Customer(
            id="cust-004",
            phone="+15550001004",
            name="David Kim",
            business_id=DEMO_BUSINESS_ID,
            is_lead=True,
            lead_stage="new",
            first_contact_at=now - timedelta(hours=1),
            last_contact_at=now - timedelta(hours=1),
            opted_in_sms=True,
            notes="Just texted about a leaking pipe. New inbound lead.",
        ),
    ]

    for c in demo_customers:
        store._customers[c.phone] = c

    # ---- Demo Conversations ----
    demo_conversations = [
        Conversation(
            id="conv-001",
            business_id=DEMO_BUSINESS_ID,
            customer_phone="+15550001001",
            agent="review_pilot",
            messages=[
                ConversationMessage(
                    role=MessageRole.USER,
                    content="Just left a 5-star review on Google!",
                    timestamp=now - timedelta(days=45),
                ),
                ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    content="Thank you so much, Alice! We're thrilled you're happy with the water heater install. We look forward to helping you again!",
                    timestamp=now - timedelta(days=45),
                ),
            ],
            summary="Customer left 5-star review; agent replied with gratitude.",
            last_message="Thank you so much, Alice! ...",
            outcome="review_responded",
            response_time_seconds=4.2,
            created_at=now - timedelta(days=45),
            updated_at=now - timedelta(days=45),
        ),
        Conversation(
            id="conv-002",
            business_id=DEMO_BUSINESS_ID,
            customer_phone="+15550001002",
            agent="lead_catcher",
            messages=[
                ConversationMessage(
                    role=MessageRole.USER,
                    content="Hi, I need help with bathroom plumbing for a renovation. How much does it cost?",
                    timestamp=now - timedelta(days=2),
                ),
                ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    content="Hi Bob! Great timing — bathroom rough-in work typically runs $800–$2,500 depending on complexity. I'd love to get Pete out to give you a free estimate. Are you available this Thursday or Friday afternoon?",
                    timestamp=now - timedelta(days=2),
                ),
            ],
            summary="Lead qualified for bathroom renovation; appointment scheduling in progress.",
            last_message="Are you available this Thursday or Friday afternoon?",
            outcome=None,
            response_time_seconds=6.1,
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=1),
        ),
        Conversation(
            id="conv-003",
            business_id=DEMO_BUSINESS_ID,
            customer_phone="+15550001004",
            agent="after_hours",
            messages=[
                ConversationMessage(
                    role=MessageRole.USER,
                    content="my pipe is leaking at 11pm, what do i do??",
                    timestamp=now - timedelta(hours=1),
                ),
                ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    content="Hi! This is Pete's Plumbing — we've got you covered 24/7. First, shut off the main water valve (usually near your meter or water heater). Text me your address and we'll have an emergency tech to you within 2 hours. Hang tight!",
                    timestamp=now - timedelta(hours=1),
                ),
            ],
            summary="After-hours emergency: leaking pipe. Customer given emergency instructions and dispatch initiated.",
            last_message="Text me your address and we'll have an emergency tech to you within 2 hours.",
            outcome="faq_answered",
            response_time_seconds=3.8,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        ),
    ]

    for conv in demo_conversations:
        store._conversations[conv.id] = conv

    logger.info(
        "Demo store seeded: 1 business, %d customers, %d conversations",
        len(demo_customers),
        len(demo_conversations),
    )
    return store


# Module-level singleton — imported by the FastAPI app and agents
demo_store = _build_demo_store()
