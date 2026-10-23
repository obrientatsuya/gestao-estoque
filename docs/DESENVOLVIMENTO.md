# V7.1: Decisoes e Validacao

## Escopo

Redesign operacional dos dois modulos, preservando os clientes Tiny existentes,
regras de custo/preco, paginacao e limites. A V7.1 acrescenta a publicacao de
unidades no Supabase com uma RPC dedicada; os GETs de estoque existentes sao
reutilizados sem escrita no Tiny. Os servidores tambem servem assets locais
`/ui/*` e configuracao publica `/ui/runtime-config.js`.
As credenciais deixaram o HTML/Python versionavel e passaram a configuracao privada.

## Interface

- Public Sans local, numerais tabulares, rotulos compactos e contraste entre dados
  principais e metadados; espacamento sem grandes areas promocionais.
- Fundo neutro, acoes em verde-petroleo e as cores estaveis de setor preservadas.
- Caixas principais retangulares, coordenada centralizada e espaco maior entre
  caixas/prateleiras; bordas seguem a ocupacao. SETOR permanece branco sobre cor.
- Tema claro sem gradiente escuro e tema escuro consistente nos dois modulos.
- Versao discreta no canto, fora da marca principal.
- Icones Lucide, incluindo o mapa de vendas no menu, foco visivel, entradas
  escalonadas de 230-320 ms, contadores de 220 ms e retorno de sucesso de 380 ms.
- Animacoes seguem `prefers-reduced-motion` por padrao; o switch do menu permite
  uma escolha explicita persistida, inclusive para desativar em qualquer sistema.
- Ocupacao pela soma de unidades disponiveis, sem editor manual. Dados ausentes
  nunca viram zero. Escalas continuas e referencia global: vendas vermelho-verde,
  ocupacao verde-vermelho, dados parciais cinza e excluidos do ranking comparativo.

Foram usadas as instrucoes locais de `revenue-centric-design` (reduzir carga
cognitiva, encurtar tarefas, estados verdadeiros e feedback rapido) e da skill
atual `taste-skill`. O upstream consultado nao fornecia a skill solicitada com o
nome `minimalist-ui`; foi usada a versao atual disponivel, sem fingir sua instalacao.
Nao foram aplicados mecanismos de venda, urgencia ou manipulacao ao fluxo interno.

## Eficiencia

- Filtro visual do mapa agrupado por animation frame, sem novas chamadas de API.
- Setores fora da tela usam `content-visibility`; componentes e fontes sao locais,
  sem dependencia de CDN em cada abertura.
- Imagens de paginas seguintes da nota usam carregamento lazy/decodificacao async.
- Quantidades tem debounce por item e uma fila de gravacao. A fila e necessaria
  porque as respostas trazem a nota inteira: uma resposta antiga nao pode restaurar
  a quantidade anterior de outro item.
- Troca de nota, impressao, desfazer e reset aguardam quantidades pendentes.
- Se uma gravacao falhar, a quantidade pendente continua disponivel para tentativa.
- Sao otimizacoes direcionadas; nao se afirma ganho percentual sem benchmark em
  carga real. Nao houve refatoracao indiscriminada da codebase.

## Validacao

- 48 testes Python com clientes simulados e SQLite temporario, incluindo o
  contrato da RPC, frescor, falhas parciais e GETs do perfil Leitor.
- 14 testes Node: fila de quantidades, dados desconhecidos, deduplicacao, decimais,
  totais por unidades e escalas lineares de vendas/ocupacao.
- Testes visuais no Chrome com fixtures sinteticas: claro/escuro, caixas, busca,
  auditoria, mapas, autocomplete com setas, conferencia, historico e configuracoes.
- Inspecao dos dois executaveis: perfis separados, sem access/refresh tokens
  empacotados e sem chave de publicacao no Leitor.
- Nenhuma impressora real ou alteracao de produto foi usada na validacao visual.

## Publicacao

`configuracao_privada/`, configs/tokens reais, executaveis, bancos, logs, PDFs,
diretorios recuperados e artefatos de build nao pertencem ao Git.
Exemplos em `config/` contem apenas placeholders. Screenshots sao sinteticas.
Licencas acompanham Public Sans e Lucide. A ferramenta de seguranca revisa o indice
Git e pode comparar bytes com as credenciais privadas da maquina sem revela-las:

```powershell
python tools/security_check.py --repo CAMINHO_DO_REPOSITORIO --private-root CAMINHO_PRIVADO
git diff --cached --stat
git commit -m "fix(v7.1): automate unit occupancy and correct heatmap feedback"
```

### Hipotese de Commit

`fix(v7.1): automate unit occupancy and correct heatmap feedback`

Hipotese: unidades no lugar de niveis manuais, cores consistentes e feedback
imediato reduzem o tempo de decidir onde armazenar itens. Validar em operacao
com tempo por cadastro/conferencia, erros corrigidos e consultas por caixa, sem
coletar telemetria automaticamente ou expor dados dos operadores.
