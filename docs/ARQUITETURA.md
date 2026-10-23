# Arquitetura e Operacao

## Responsabilidades

| Arquivo | Responsabilidade |
| --- | --- |
| `unified_v7.py` | Instancia unica e inicializacao dos dois servidores |
| `sync_erp_lote.py` | Servidor Coordenadas, sincronizacao, auditoria e rotas locais |
| `HL_COORDENADAS_v7.html` | Interface principal, mapa, busca e auditoria |
| `runtime_config.py` | Configuracao publica externa ao codigo |
| `app_edition.py` | Perfil Leitor/Publicador embutido no pacote |
| `box_insights.py` | Cache SQLite, consultas sob demanda e agregacao existente |
| `shared_sales.py` | Leitura/publicacao do resumo compartilhado |
| `box_occupancy.py` | Validacao de estoque recente e publicacao limitada das unidades |
| `ui/warehouse-metrics.js` | Totais deduplicados, dados desconhecidos e escalas continuas |
| `enviar_localizacoes_tiny_v3.py`, `tiny_v3_auth.py` | Cliente e OAuth Tiny existentes |
| `HL_Conferencia_NFe/` | Parser PDF, progresso SQLite, exportacao e impressao |
| `ui/` | Tokens visuais, fontes, icones e feedback local dos dois modulos |
| `tools/demo_server.py` | Demonstracao inteiramente isolada, sem integracoes reais |

## Fluxos

1. **Localizacao**: a interface consulta/salva no Supabase. Ao sincronizar, a ponte
   rele as localizacoes atuais e envia o campo de localizacao ao Tiny pelo fluxo
   existente. ERP e Supabase sao sistemas distintos.
2. **Resumo de vendas**: no Publicador, o operador inicia a consulta dos SKUs. O
   Tiny e acessado por GET para produtos, estoque e logs; a renovacao OAuth usa
   o POST oficial de tokens. Os dados sao agregados localmente e somente o resumo
   por SKU e enviado por POST a RPC restrita `publicar_resumo_vendas` no Supabase.
3. **Leitor**: caixas e mapa usam o resumo compartilhado. Sem resumo nao significa
   que nao houve venda. Atualizar nao inicia uma importacao de notas fiscais.
4. **Conferencia**: PDFs permanecem no PC. Itens e progresso ficam no SQLite local;
   o PDF final e gerado com as marcacoes, sem sobrescrever o documento de entrada.
5. **Ocupacao**: somente Atualizar de uma caixa inicia GETs de produto/estoque no
   Tiny. Todas as consultas precisam ter sucesso depois do inicio do trabalho.
   A RPC `atualizar_ocupacao_caixa` confirma a composicao da caixa sob bloqueio de
   linhas e rejeita uma consulta mais antiga. Ela escreve apenas unidades e data;
   nao altera localizacao, operador, datas de movimentacao, custo ou preco.
   O Leitor tambem pode atualizar essas unidades se tiver autorizacao Tiny local,
   sem usar logs ou publicar vendas. Abrir uma caixa apenas le dados em cache.

Nao confunda "consulta de vendas somente GET no Tiny" com "toda aplicacao somente
leitura": cadastrar/mover/excluir um SKU no Coordenadas e alterar localizacao no
Tiny sao operacoes de escrita deliberadas, ja existentes.

## Supabase

- `supabase_migracao.sql`: historico e estrutura de localizacao.
- `supabase_resumo_vendas.sql`: tabela de agregados e RPC limitada ao resumo.
- `supabase_chave_publicador.sql`: geracao/configuracao administrativa da chave.
- `supabase_ocupacao_unidades.sql`: unidades por SKU e RPC SECURITY INVOKER, com
  as permissoes/RLS existentes. Uma caixa incompleta permanece desconhecida e nao
  entra no ranking. As cores comparam valores absolutos com a maior caixa completa,
  de forma linear; nao inferem volume fisico a partir da quantidade.
- Nao inserir `service_role` em HTML, runtime-config.js ou executaveis de operadores.
- A leitura anonima do resumo foi uma decisao do fluxo interno; ela nao e privada
  para quem conhece URL/chave publica do projeto. Revisar RLS antes de uso externo.
- Historico local nao comprova acoes realizadas antes da auditoria nem todos os
  eventos executados fora do programa. "Ausencia detectada" nao identifica autor.

## Persistencia e Atualizacao

O perfil Publicador tem pasta separada; o Leitor reutiliza a pasta historica V5.
A V7 nao migra nem apaga essas pastas. O progresso da conferencia e independente.
O mutex historico tambem foi mantido para nao abrir versoes concorrentes.

Se outro EXE antigo ainda estiver aberto, feche-o pelo console com `SAIR` antes
de abrir o novo. A instancia unica pode apenas trazer a interface ja aberta;
nao assuma que um duplo clique substituiu uma versao que estava rodando.
O reinicio no modulo NFe e o mecanismo existente do servidor, nao um atualizador
que baixa um EXE novo. Distribua a compilacao nova para atualizar outro PC.

Para transferencia de dados, feche o programa e copie as pastas de dados e os
PDFs originais para o destino autorizado. Tokens OAuth sao locais e devem ser
reautorizados; nao os envie junto com screenshots ou codigo.

## Diagnostico

- **403 nos logs**: indica falta de acesso do usuario/aplicativo nessa chamada;
  nao publicar zero como substituto. A V7 nao contorna permissoes do Tiny.
- **Porta ocupada**: o programa nao encerra processos desconhecidos; encerre a
  instancia correta antes de reabrir.
- **Sem dados de vendas**: confira o periodo/horario do resumo e se o Publicador
  concluiu a consulta. O Leitor nao fabrica resultados nem usa notas como fallback.
- **Impressao**: selecione a impressora em Configuracoes. O runtime confere o hash
  do SumatraPDF. Fonte/orientacao continuam obedecendo ao modelo existente.
- **PDF diferente**: a extracao depende do layout textual. Compare SKU, quantidade
  e marcacao antes de liberar um layout novo; PDFs escaneados podem exigir OCR,
  que nao foi adicionado nesta versao.
