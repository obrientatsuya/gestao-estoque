# Resumo de vendas compartilhado

## 1. Criar a tabela

No projeto Supabase usado pelo HL, abra SQL Editor, cole e execute
`supabase_resumo_vendas.sql`. Depois execute `supabase_chave_publicador.sql`
e guarde a chave retornada. A segunda consulta nao troca uma chave ja criada.

## 2. Configurar somente o PC autorizado

Crie `%LOCALAPPDATA%\HL_Coordenadas_V5\vendas_publicador.json` com:

```json
{"chave": "CHAVE_RETORNADA_PELO_SQL"}
```

Para executar pelo Python, o arquivo fica na pasta do projeto, ou use a variavel
de ambiente `HL_VENDAS_PUBLICADOR`. Nao coloque a chave no HTML ou no executavel
distribuido aos leitores. Nao e necessario usar service_role.

Autorize o Tiny neste PC. Em Mais > Atualizar vendas > Consultar logs e publicar,
o programa busca os SKUs cadastrados em Coordenadas, consulta produtos/estoque/logs
usando somente GET e publica uma linha agregada por SKU. O botao Atualizar de uma
caixa faz isso somente para seus SKUs. Nao usa notas fiscais.

## 3. Outros PCs

Use o executavel atualizado, sem vendas_publicador.json. As caixas leem
resumo_vendas sem login no Tiny. Atualizar nesses PCs so rele o Supabase.
Nomes e estoque disponivel continuam sendo dados do cache local; nao sao
publicados nesta tabela, conforme o pedido de compartilhar somente o resumo.

## Dados e limites

- Uma linha por SKU distinto: 4.000 SKUs = 4.000 linhas, nao 4.000 logs.
- vendas = quantidade de movimentos de saida com valor positivo, deduplicados
  por identificador. Nao e quantidade de unidades nem pedidos unicos.
- unidades = soma das quantidades desses movimentos.
- Periodo: 2000-01-01 ate a data da consulta, salvo em inicio/fim.
- Somente resumo e metadados de periodo/atualizacao vao ao Supabase. Nenhum token,
  log bruto, custo, preco, cliente, nota ou pedido e publicado.
- Falha/403 ou campo de valor ausente nao publica zero e nao substitui resumo.
- A leitura anonima e publica a quem tiver acesso a API do projeto. A publicacao
  exige chave propria, limitada pela funcao SQL a esta tabela. Nao ha DELETE.
- O mapa soma as vendas dos SKUs que estao atualmente em cada caixa: nao e um
  historico das localizacoes na data de cada venda. Caixas sem todos os resumos
  ficam parciais e nao entram no ranking comparativo de caixas completas.
- As opcoes de importar notas estao ocultas, e a rota de inicio foi desativada.
- O 403 dos logs ainda precisa ser resolvido na autorizacao Tiny deste PC.

O sistema nao publica nem faz consulta em massa ao abrir. So inicia pelos botoes.
