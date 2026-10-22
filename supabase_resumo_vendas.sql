-- Execute no SQL Editor do seu projeto Supabase. Nao altera estoque ou notas.
create table if not exists public.resumo_vendas (
  sku text primary key,
  vendas bigint not null check (vendas >= 0),
  unidades numeric not null check (unidades >= 0),
  inicio date not null,
  fim date not null check (fim >= inicio),
  calculado_em timestamptz not null,
  atualizado_em timestamptz not null default now(),
  fonte text not null default 'logs_saidas_valor_positivo'
);
alter table public.resumo_vendas enable row level security;
revoke all on public.resumo_vendas from anon, authenticated;
grant select on public.resumo_vendas to anon, authenticated;
drop policy if exists leitura_resumo_vendas on public.resumo_vendas;
create policy leitura_resumo_vendas on public.resumo_vendas for select to anon, authenticated using (true);

-- A chave do publicador nao pode ser consultada pela API publica.
create schema if not exists hl_privado;
revoke all on schema hl_privado from public, anon, authenticated;
create table if not exists hl_privado.publicador_vendas (
  id boolean primary key default true check (id),
  chave_hash text not null
);
revoke all on hl_privado.publicador_vendas from public, anon, authenticated;

create or replace function public.publicar_resumo_vendas(p_chave text, p_resumos jsonb)
returns setof public.resumo_vendas
language plpgsql security definer set search_path = '' as $$
begin
  if p_chave is null or not exists (
    select 1 from hl_privado.publicador_vendas
    where chave_hash = encode(sha256(convert_to(p_chave, 'UTF8')), 'hex')
  ) then
    raise insufficient_privilege using message = 'Publicador nao autorizado';
  end if;
  if jsonb_typeof(p_resumos) <> 'array' or jsonb_array_length(p_resumos) not between 1 and 150 then
    raise exception 'Lote invalido';
  end if;
  if exists (select 1 from jsonb_to_recordset(p_resumos) as r(sku text, calculado_em timestamptz)
             where r.sku is null or length(r.sku) not between 1 and 100
               or r.sku <> upper(trim(r.sku)) or r.calculado_em is null
               or r.calculado_em > now() + interval '5 minutes') then
    raise exception 'Resumo invalido';
  end if;
  return query
    insert into public.resumo_vendas as atual(sku,vendas,unidades,inicio,fim,calculado_em)
    select r.sku,r.vendas,r.unidades,r.inicio,r.fim,r.calculado_em
    from jsonb_to_recordset(p_resumos) as r(sku text,vendas bigint,unidades numeric,inicio date,fim date,calculado_em timestamptz)
    on conflict (sku) do update set vendas=excluded.vendas,unidades=excluded.unidades,
      inicio=excluded.inicio,fim=excluded.fim,calculado_em=excluded.calculado_em,atualizado_em=now()
    where excluded.calculado_em >= atual.calculado_em
    returning atual.*;
end;
$$;
revoke all on function public.publicar_resumo_vendas(text,jsonb) from public;
grant execute on function public.publicar_resumo_vendas(text,jsonb) to anon, authenticated;

-- Execute este bloco UMA VEZ, separado do script acima. Copie a chave exibida
-- somente para o arquivo vendas_publicador.json do PC autorizado.
-- Nao execute novamente sem querer trocar a chave do publicador.
-- do $$ declare chave text := replace(gen_random_uuid()::text,'-','') || replace(gen_random_uuid()::text,'-','');
-- begin
--   insert into hl_privado.publicador_vendas(id,chave_hash)
--   values(true,encode(sha256(convert_to(chave,'UTF8')),'hex'))
--   on conflict(id) do update set chave_hash=excluded.chave_hash;
--   raise notice 'CHAVE DO PUBLICADOR: %', chave;
-- end $$;
