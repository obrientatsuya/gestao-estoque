-- HL Service Localizador — executar no Supabase (SQL Editor)
-- Adiciona colunas de sync ERP, capacidade por prateleira e tabela de historico.

ALTER TABLE estoque
  ADD COLUMN IF NOT EXISTS sincronizado_erp_em timestamptz,
  ADD COLUMN IF NOT EXISTS erro_erp text;

ALTER TABLE estrutura
  ADD COLUMN IF NOT EXISTS capacidade_sku integer DEFAULT 8;

CREATE TABLE IF NOT EXISTS historico_movimentos (
  id bigserial PRIMARY KEY,
  sku text NOT NULL,
  operador text,
  posicao_anterior text,
  posicao_nova text,
  acao text NOT NULL,
  criado_em timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_historico_criado_em ON historico_movimentos (criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_historico_sku ON historico_movimentos (sku);
CREATE INDEX IF NOT EXISTS idx_historico_sku_criado_em ON historico_movimentos (sku, criado_em DESC);

-- RLS: ajuste conforme sua politica de seguranca (exemplo permissivo para chave anon):
-- ALTER TABLE historico_movimentos ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "anon_all" ON historico_movimentos FOR ALL USING (true) WITH CHECK (true);
