-- Execute UMA VEZ depois de supabase_resumo_vendas.sql.
-- Se ja houver chave, nao a troca e nao retorna outra. Guarde a chave exibida.
with nova as materialized (
  select replace(gen_random_uuid()::text,'-','') || replace(gen_random_uuid()::text,'-','') as chave
), inserida as (
  insert into hl_privado.publicador_vendas(id,chave_hash)
  select true,encode(sha256(convert_to(chave,'UTF8')),'hex') from nova
  on conflict(id) do nothing returning id
)
select chave as chave_do_pc_publicador from nova,inserida;
