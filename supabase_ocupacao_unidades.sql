-- Execute uma vez no SQL Editor. Nao modifica os valores ja cadastrados.
alter table public.estoque add column if not exists unidades_estoque numeric;
alter table public.estoque add column if not exists unidades_atualizadas_em timestamptz;
comment on column public.estoque.unidades_estoque is 'Unidades disponiveis no Tiny, consultadas explicitamente por caixa. NULL = nao consultado.';

create or replace function public.atualizar_ocupacao_caixa(
  p_setor text,p_prateleira text,p_caixa text,p_itens jsonb,p_consultado_em timestamptz
) returns table(sku text,unidades_estoque numeric,unidades_atualizadas_em timestamptz)
language plpgsql security invoker set search_path = '' as $$
declare quantidade integer;
begin
  if p_setor !~ '^[1-9][0-9]*S$' or p_prateleira !~ '^P[1-9][0-9]*$' or p_caixa !~ '^C[1-9][0-9]*$'
    or p_setor is null or p_prateleira is null or p_caixa is null then
    raise exception 'Coordenada invalida';
  end if;
  if jsonb_typeof(p_itens) is distinct from 'array' then raise exception 'Lista de SKUs invalida'; end if;
  quantidade := jsonb_array_length(p_itens);
  if quantidade not between 1 and 150 or p_consultado_em is null or p_consultado_em > now()+interval '5 minutes' then
    raise exception 'Consulta invalida';
  end if;
  if exists(select 1 from jsonb_to_recordset(p_itens) as r(sku text,unidades numeric)
      where r.sku is null or r.sku <> upper(trim(r.sku)) or length(r.sku) not between 1 and 100
         or r.unidades is null or r.unidades < 0 or r.unidades > 1000000000000
         or r.unidades::text in ('NaN','Infinity','-Infinity'))
     or (select count(distinct r.sku) from jsonb_to_recordset(p_itens) as r(sku text)) <> quantidade then
    raise exception 'Quantidade de unidades invalida';
  end if;
  -- Trava a caixa e confirma sua composicao antes de gravar todos os SKUs juntos.
  perform e.sku from public.estoque e
    where e.setor=p_setor and e.prateleira=p_prateleira and e.caixa=p_caixa order by e.sku for update;
  if (select count(*) from public.estoque e where e.setor=p_setor and e.prateleira=p_prateleira and e.caixa=p_caixa) <> quantidade
     or exists(select 1 from jsonb_to_recordset(p_itens) as r(sku text)
       where not exists(select 1 from public.estoque e where e.sku=r.sku and e.setor=p_setor and e.prateleira=p_prateleira and e.caixa=p_caixa)) then
    raise exception 'A composicao da caixa mudou. Reabra a caixa e atualize novamente.';
  end if;
  if exists(select 1 from public.estoque e where e.setor=p_setor and e.prateleira=p_prateleira and e.caixa=p_caixa
       and e.unidades_atualizadas_em > p_consultado_em) then
    raise exception 'Ja existe uma consulta mais recente. Valores preservados.';
  end if;
  return query update public.estoque e
    set unidades_estoque=r.unidades,unidades_atualizadas_em=p_consultado_em
    from jsonb_to_recordset(p_itens) as r(sku text,unidades numeric)
    where e.sku=r.sku and e.setor=p_setor and e.prateleira=p_prateleira and e.caixa=p_caixa
    returning e.sku,e.unidades_estoque,e.unidades_atualizadas_em;
end;
$$;
revoke all on function public.atualizar_ocupacao_caixa(text,text,text,jsonb,timestamptz) from public;
grant execute on function public.atualizar_ocupacao_caixa(text,text,text,jsonb,timestamptz) to anon, authenticated;
-- SECURITY INVOKER: mantem as permissoes e RLS ja existentes de cada operador.
notify pgrst,'reload schema';
