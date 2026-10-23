-- Execute uma vez no SQL Editor. Nao apaga nem move SKUs existentes.
create table if not exists public.caixas_bloqueadas (
  setor text not null, prateleira text not null, caixa text not null,
  bloqueada boolean not null default false,
  atualizado_em timestamptz not null default now(),
  primary key(setor,prateleira,caixa)
);
alter table public.caixas_bloqueadas enable row level security;
revoke all on public.caixas_bloqueadas from anon,authenticated;
grant select on public.caixas_bloqueadas to anon,authenticated;
drop policy if exists leitura_bloqueio_caixas on public.caixas_bloqueadas;
create policy leitura_bloqueio_caixas on public.caixas_bloqueadas for select to anon,authenticated using(true);

create or replace function public.definir_bloqueio_caixa(p_setor text,p_prateleira text,p_caixa text,p_bloqueada boolean)
returns setof public.caixas_bloqueadas
language plpgsql security definer set search_path='' as $$
begin
  if p_setor is null or p_prateleira is null or p_caixa is null or p_bloqueada is null
     or p_setor !~ '^[1-9][0-9]*S$' or p_prateleira !~ '^P[1-9][0-9]*$' or p_caixa !~ '^C[1-9][0-9]*$' then
    raise exception 'Coordenada invalida';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(concat_ws('|',p_setor,p_prateleira,p_caixa),0));
  return query insert into public.caixas_bloqueadas as c(setor,prateleira,caixa,bloqueada)
    values(p_setor,p_prateleira,p_caixa,p_bloqueada)
    on conflict(setor,prateleira,caixa) do update set bloqueada=excluded.bloqueada,atualizado_em=now()
    returning c.*;
end;
$$;
revoke all on function public.definir_bloqueio_caixa(text,text,text,boolean) from public;
grant execute on function public.definir_bloqueio_caixa(text,text,text,boolean) to anon,authenticated;

create or replace function public.impedir_entrada_caixa_bloqueada()
returns trigger language plpgsql security definer set search_path='' as $$
begin
  if TG_OP='UPDATE' then
    if (new.setor,new.prateleira,new.caixa) is not distinct from (old.setor,old.prateleira,old.caixa) then return new; end if;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(concat_ws('|',new.setor,new.prateleira,new.caixa),0));
  if exists(select 1 from public.caixas_bloqueadas c where c.setor=new.setor and c.prateleira=new.prateleira
      and c.caixa=new.caixa and c.bloqueada) then
    raise exception using errcode='P0001',message='CAIXA_BLOQUEADA: esta caixa nao aceita novos SKUs';
  end if;
  return new;
end;
$$;
revoke all on function public.impedir_entrada_caixa_bloqueada() from public;
drop trigger if exists verificar_bloqueio_caixa on public.estoque;
create trigger verificar_bloqueio_caixa before insert or update of setor,prateleira,caixa on public.estoque
  for each row execute function public.impedir_entrada_caixa_bloqueada();
notify pgrst,'reload schema';
