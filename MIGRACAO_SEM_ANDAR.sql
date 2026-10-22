-- HL SERVICE - MIGRACAO PARA POSICAO SEM ANDAR
-- Novo padrao de localizacao:
--   6S-P3C2   = setor 6, prateleira 3, caixa 2, sem letra
--   6S-P3C2B  = mesma caixa, lado/letra B explicitamente informado
--
-- Rode este arquivo no SQL Editor do Supabase.
-- Ele cria backups antes de consolidar os dados.

BEGIN;

-- 1) Backups de seguranca antes da migracao.
CREATE TABLE IF NOT EXISTS estoque_backup_pre_sem_andar_20260701 AS
  TABLE public.estoque;

CREATE TABLE IF NOT EXISTS estrutura_backup_pre_sem_andar_20260701 AS
  TABLE public.estrutura;

ALTER TABLE public.estrutura
  ADD COLUMN IF NOT EXISTS capacidade_sku integer DEFAULT 8;

-- 2) Estoque: remove a divisao por andar e transforma lado A em "sem letra".
--    Letras B, C, D... continuam explicitas.
UPDATE public.estoque
SET
  andar = NULL,
  lado = CASE
    WHEN NULLIF(TRIM(COALESCE(lado, '')), '') IS NULL THEN ''
    WHEN UPPER(TRIM(lado)) = 'A' THEN ''
    ELSE UPPER(TRIM(lado))
  END;

-- 3) Estrutura: remove a obrigatoriedade do andar e consolida duplicados
--    de 2A/3A em uma unica linha por setor + prateleira.
ALTER TABLE public.estrutura
  ALTER COLUMN andar DROP NOT NULL;

ALTER TABLE public.estrutura
  DROP CONSTRAINT IF EXISTS estrutura_andar_setor_prateleira_key;

WITH consolidados AS (
  SELECT
    MIN(id) AS id_mantido,
    setor,
    prateleira,
    MAX(caixas) AS caixas,
    MAX(COALESCE(capacidade_sku, 8)) AS capacidade_sku
  FROM public.estrutura
  GROUP BY setor, prateleira
),
atualizados AS (
  UPDATE public.estrutura e
  SET
    andar = NULL,
    caixas = c.caixas,
    capacidade_sku = c.capacidade_sku
  FROM consolidados c
  WHERE e.id = c.id_mantido
  RETURNING e.id
)
DELETE FROM public.estrutura e
USING consolidados c
WHERE e.setor = c.setor
  AND e.prateleira = c.prateleira
  AND e.id <> c.id_mantido;

DROP INDEX IF EXISTS public.idx_estrutura_andar_setor;
DROP INDEX IF EXISTS public.estrutura_setor_prateleira_uidx;

CREATE UNIQUE INDEX estrutura_setor_prateleira_uidx
  ON public.estrutura (setor, prateleira);

CREATE INDEX IF NOT EXISTS idx_estrutura_setor
  ON public.estrutura (setor);

-- 4) Garante campos auxiliares usados pelo app atual.
ALTER TABLE public.estoque
  ADD COLUMN IF NOT EXISTS sincronizado_erp_em timestamptz,
  ADD COLUMN IF NOT EXISTS erro_erp text,
  ADD COLUMN IF NOT EXISTS nivel TEXT NOT NULL DEFAULT 'pouca'
    CHECK (nivel IN ('vazio', 'pouca', 'media', 'quase'));

UPDATE public.estoque
SET nivel = 'pouca'
WHERE nivel IS NULL OR nivel NOT IN ('vazio', 'pouca', 'media', 'quase');

CREATE INDEX IF NOT EXISTS idx_estoque_nivel ON public.estoque (nivel);

CREATE TABLE IF NOT EXISTS public.historico_movimentos (
  id bigserial PRIMARY KEY,
  sku text NOT NULL,
  operador text,
  posicao_anterior text,
  posicao_nova text,
  acao text NOT NULL,
  criado_em timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_historico_criado_em
  ON public.historico_movimentos (criado_em DESC);

CREATE INDEX IF NOT EXISTS idx_historico_sku
  ON public.historico_movimentos (sku);

COMMIT;

-- Conferencias rapidas depois de rodar:
-- select sku, andar, setor, prateleira, caixa, lado from estoque limit 20;
-- select setor, prateleira, caixas, capacidade_sku from estrutura order by setor, prateleira;
