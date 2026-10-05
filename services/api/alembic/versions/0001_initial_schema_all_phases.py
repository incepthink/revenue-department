"""initial schema (all phases)

Every table from the spec is created here; later phases only add rows (and additive migrations).

Revision ID: 0001
Revises: 
Create Date: 2026-09-26 16:18:50.240707
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE SCHEMA IF NOT EXISTS vault")
    op.create_table('asr_rates',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('division', sa.Text(), nullable=True),
    sa.Column('district', sa.Text(), nullable=False),
    sa.Column('taluka', sa.Text(), nullable=True),
    sa.Column('village', sa.Text(), nullable=False),
    sa.Column('zone', sa.String(length=32), nullable=True),
    sa.Column('sub_zone', sa.String(length=32), nullable=True),
    sa.Column('cts_range', sa.Text(), nullable=True),
    sa.Column('land_rate_per_sqm', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('residential_rate_per_sqm', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('commercial_rate_per_sqm', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_asr_rates_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_asr_rates_lookup', 'asr_rates', ['year', 'district', 'village', 'zone'], unique=False)
    op.create_table('clause_library',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('clause_key', sa.String(length=96), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('text_mr', sa.Text(), nullable=False),
    sa.Column('template', sa.Text(), nullable=True),
    sa.Column('slots', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('rule_set_codes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('source_case_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('cluster_id', sa.Integer(), nullable=True),
    sa.Column('canonical', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='candidate', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('eval_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('code_version', sa.String(length=64), nullable=True),
    sa.Column('report_blob_key', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('event_catalog',
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('catalog_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('name_en', sa.Text(), nullable=False),
    sa.Column('name_mr', sa.Text(), nullable=False),
    sa.Column('actor', sa.String(length=32), nullable=False),
    sa.Column('typical_order', sa.Integer(), nullable=False),
    sa.Column('source_exhibit_types', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('date_field', sa.String(length=64), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('exhibit_types',
    sa.Column('code', sa.String(length=48), nullable=False),
    sa.Column('name_en', sa.Text(), nullable=False),
    sa.Column('name_mr', sa.Text(), nullable=False),
    sa.Column('default_stage', sa.String(length=24), nullable=False),
    sa.Column('party', sa.String(length=16), nullable=False),
    sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('negative_keywords', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('extraction_schema', sa.String(length=64), nullable=True),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('catalog_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('offices',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('name_mr', sa.Text(), nullable=True),
    sa.Column('division', sa.Text(), nullable=True),
    sa.Column('district', sa.Text(), nullable=True),
    sa.Column('taluka', sa.Text(), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('is_pilot', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_offices_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('requirement_catalog',
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('catalog_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('name_en', sa.Text(), nullable=False),
    sa.Column('name_mr', sa.Text(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('satisfied_by', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('query_keywords', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('applies_when', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_requirement_catalog_status'),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('rule_sets',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('notification_ref', sa.Text(), nullable=True),
    sa.Column('notification_date', sa.Date(), nullable=True),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('kind', sa.String(length=24), server_default='base', nullable=False),
    sa.Column('supersedes_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('source_blob_key', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_rule_sets_status'),
    sa.ForeignKeyConstraint(['supersedes_id'], ['rule_sets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('cases',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=False),
    sa.Column('case_ref', sa.String(length=128), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('case_type', sa.String(length=48), server_default='CHS_CLASS2_TO_CLASS1', nullable=False),
    sa.Column('data_class', sa.String(length=16), nullable=False),
    sa.Column('role', sa.String(length=16), server_default='unassigned', nullable=False),
    sa.Column('role_locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='created', nullable=False),
    sa.Column('society_name', sa.Text(), nullable=True),
    sa.Column('filed_on', sa.Date(), nullable=True),
    sa.Column('decided_on', sa.Date(), nullable=True),
    sa.Column('rule_set_id', sa.UUID(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("data_class IN ('real','synthetic')", name='ck_cases_data_class'),
    sa.CheckConstraint("role IN ('unassigned','precedent','holdout','incoming')", name='ck_cases_role'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], ),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('office_id', 'case_ref', name='uq_cases_office_ref')
    )
    op.create_table('ocr_benchmark_results',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('sample_id', sa.String(length=128), nullable=False),
    sa.Column('exhibit_type', sa.String(length=48), nullable=False),
    sa.Column('engine', sa.String(length=32), nullable=False),
    sa.Column('ref_chars', sa.Integer(), nullable=False),
    sa.Column('edit_distance', sa.Integer(), nullable=False),
    sa.Column('cer', sa.Float(), nullable=False),
    sa.Column('mean_confidence', sa.Float(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rule_adjustments',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('rule_set_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('rate_pct', sa.Numeric(precision=7, scale=4), nullable=True),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('applies_from', sa.Date(), nullable=True),
    sa.Column('applies_to', sa.Date(), nullable=True),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_rule_adjustments_status'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rule_obligations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('rule_set_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('description_mr', sa.Text(), nullable=True),
    sa.Column('description_en', sa.Text(), nullable=True),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_rule_obligations_status'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rule_premium_slabs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('rule_set_id', sa.UUID(), nullable=False),
    sa.Column('applicant_category', sa.String(length=32), nullable=False),
    sa.Column('holding_basis', sa.String(length=32), nullable=False),
    sa.Column('land_use', sa.String(length=32), nullable=False),
    sa.Column('window_from', sa.Date(), nullable=True),
    sa.Column('window_to', sa.Date(), nullable=True),
    sa.Column('rate_pct', sa.Numeric(precision=7, scale=4), nullable=False),
    sa.Column('rate_basis', sa.String(length=48), nullable=False),
    sa.Column('min_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('max_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_rule_premium_slabs_status'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rule_requirements',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('rule_set_id', sa.UUID(), nullable=False),
    sa.Column('requirement_code', sa.String(length=64), nullable=False),
    sa.Column('mandatory', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='to_verify', nullable=False),
    sa.Column('source_ref', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('verified','to_verify')", name='ck_rule_requirements_status'),
    sa.ForeignKeyConstraint(['requirement_code'], ['requirement_catalog.code'], ),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('office_id', sa.UUID(), nullable=True),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('reviewer','officer','admin')", name='ck_users_role'),
    sa.ForeignKeyConstraint(['office_id'], ['offices.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_kind', sa.String(length=16), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=48), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('case_id', sa.UUID(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_log_case', 'audit_log', ['case_id', 'occurred_at'], unique=False)
    op.create_table('case_cards',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.String(length=16), server_default='card.v1', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('card', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('approved_by', sa.UUID(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parent_card_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft','approved','superseded')", name='ck_case_cards_status'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['parent_card_id'], ['case_cards.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'version', name='uq_case_cards_case_version')
    )
    op.create_table('documents',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('blob_key', sa.Text(), nullable=False),
    sa.Column('parent_document_id', sa.UUID(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=24), server_default='uploaded', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'sha256', name='uq_documents_case_sha')
    )
    op.create_table('drafts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('template_code', sa.String(length=48), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('render_mode', sa.String(length=16), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('blob_key', sa.Text(), nullable=True),
    sa.Column('officer_upload_blob_key', sa.Text(), nullable=True),
    sa.Column('officer_diff', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='generated', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'template_code', 'version', 'render_mode', name='uq_drafts_version')
    )
    op.create_table('eval_case_results',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=True),
    sa.Column('stratum', sa.String(length=64), nullable=True),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('outputs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('eval_labels',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('target_case_id', sa.UUID(), nullable=True),
    sa.Column('label_kind', sa.String(length=32), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('labeller_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['labeller_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['target_case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('exhibits',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('exhibit_type', sa.String(length=48), nullable=False),
    sa.Column('stage', sa.String(length=24), nullable=False),
    sa.Column('start_page_seq', sa.Integer(), nullable=False),
    sa.Column('end_page_seq', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('classification_method', sa.String(length=16), nullable=True),
    sa.Column('origin', sa.String(length=16), server_default='auto', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('doc_date', sa.Date(), nullable=True),
    sa.Column('extraction', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("stage IN ('filing','query_response','internal','final')", name='ck_exhibits_stage'),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['exhibit_type'], ['exhibit_types.code'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_exhibits_case_seq', 'exhibits', ['case_id', 'seq'], unique=False)
    op.create_table('pipeline_steps',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.String(length=80), nullable=False),
    sa.Column('step', sa.String(length=32), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('pipeline_version', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('runs', sa.Integer(), server_default='1', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'scope', 'step', name='uq_pipeline_steps_scope_step')
    )
    op.create_table('pii_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=False),
    sa.Column('value_hmac', sa.LargeBinary(), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('nonce', sa.LargeBinary(), nullable=False),
    sa.Column('key_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'entity_type', 'value_hmac', name='uq_pii_tokens_value'),
    sa.UniqueConstraint('case_id', 'token', name='uq_pii_tokens_token'),
    schema='vault'
    )
    op.create_table('case_embeddings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('facet', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=True),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("facet IN ('facts','documents','issues')", name='ck_case_embeddings_facet'),
    sa.ForeignKeyConstraint(['card_id'], ['case_cards.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('card_id', 'facet', 'model', name='uq_case_embeddings_card_facet_model')
    )
    op.create_table('case_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('event_code', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('event_date', sa.Date(), nullable=True),
    sa.Column('exhibit_id', sa.UUID(), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('origin', sa.String(length=16), server_default='auto', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['case_cards.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_code'], ['event_catalog.code'], ),
    sa.ForeignKeyConstraint(['exhibit_id'], ['exhibits.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('case_requirements',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=False),
    sa.Column('requirement_code', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('satisfied_by_exhibit_id', sa.UUID(), nullable=True),
    sa.Column('was_queried_in', sa.UUID(), nullable=True),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('origin', sa.String(length=16), server_default='auto', nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('present','missing','invalid','queried','waived','not_applicable')", name='ck_case_requirements_status'),
    sa.ForeignKeyConstraint(['card_id'], ['case_cards.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_code'], ['requirement_catalog.code'], ),
    sa.ForeignKeyConstraint(['satisfied_by_exhibit_id'], ['exhibits.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['was_queried_in'], ['exhibits.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('card_id', 'requirement_code', name='uq_case_requirements_card_code')
    )
    op.create_table('extraction_corrections',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('draft_card_id', sa.UUID(), nullable=False),
    sa.Column('approved_card_id', sa.UUID(), nullable=False),
    sa.Column('exhibit_type', sa.String(length=48), nullable=True),
    sa.Column('field_path', sa.Text(), nullable=False),
    sa.Column('draft_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('approved_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_card_id'], ['case_cards.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['draft_card_id'], ['case_cards.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('fee_calculations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=True),
    sa.Column('rule_set_id', sa.UUID(), nullable=True),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('trace', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_amount', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('depends_on_unverified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['case_cards.id'], ),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_sets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('pages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=True),
    sa.Column('image_sha256', sa.String(length=64), nullable=True),
    sa.Column('image_blob_key', sa.Text(), nullable=True),
    sa.Column('width_px', sa.Integer(), nullable=True),
    sa.Column('height_px', sa.Integer(), nullable=True),
    sa.Column('dpi', sa.Integer(), nullable=True),
    sa.Column('text_layer_status', sa.String(length=16), nullable=True),
    sa.Column('text_layer_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ocr_engine', sa.String(length=32), nullable=True),
    sa.Column('ocr_confidence', sa.Float(), nullable=True),
    sa.Column('ocr_blob_key', sa.Text(), nullable=True),
    sa.Column('ocr_status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('text_redacted', sa.Text(), nullable=True),
    sa.Column('text_redacted_ascii', sa.Text(), nullable=True),
    sa.Column('text_sha256', sa.String(length=64), nullable=True),
    sa.Column('pii_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('classification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('predicted_type', sa.String(length=48), nullable=True),
    sa.Column('predicted_confidence', sa.Float(), nullable=True),
    sa.Column('pipeline_version', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'page_no', name='uq_pages_doc_page')
    )
    op.create_index('ix_pages_case_seq', 'pages', ['case_id', 'seq'], unique=False)
    op.create_table('review_assignments',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('reviewer_id', sa.UUID(), nullable=False),
    sa.Column('mode', sa.String(length=16), server_default='single', nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='assigned', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['case_cards.id'], ),
    sa.ForeignKeyConstraint(['case_id'], ['cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('exhibit_pages',
    sa.Column('exhibit_id', sa.UUID(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['exhibit_id'], ['exhibits.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('exhibit_id', 'page_id')
    )
    op.execute("CREATE INDEX ix_case_embeddings_hnsw ON case_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_clause_library_hnsw ON clause_library USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.drop_table('exhibit_pages')
    op.drop_table('review_assignments')
    op.drop_index('ix_pages_case_seq', table_name='pages')
    op.drop_table('pages')
    op.drop_table('fee_calculations')
    op.drop_table('extraction_corrections')
    op.drop_table('case_requirements')
    op.drop_table('case_events')
    op.drop_table('case_embeddings')
    op.drop_table('pii_tokens', schema='vault')
    op.drop_table('pipeline_steps')
    op.drop_index('ix_exhibits_case_seq', table_name='exhibits')
    op.drop_table('exhibits')
    op.drop_table('eval_labels')
    op.drop_table('eval_case_results')
    op.drop_table('drafts')
    op.drop_table('documents')
    op.drop_table('case_cards')
    op.drop_index('ix_audit_log_case', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('users')
    op.drop_table('rule_requirements')
    op.drop_table('rule_premium_slabs')
    op.drop_table('rule_obligations')
    op.drop_table('rule_adjustments')
    op.drop_table('ocr_benchmark_results')
    op.drop_table('cases')
    op.drop_table('rule_sets')
    op.drop_table('requirement_catalog')
    op.drop_table('offices')
    op.drop_table('exhibit_types')
    op.drop_table('event_catalog')
    op.drop_table('eval_runs')
    op.drop_table('clause_library')
    op.drop_index('ix_asr_rates_lookup', table_name='asr_rates')
    op.drop_table('asr_rates')
