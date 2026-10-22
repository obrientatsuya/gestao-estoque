# Duas edicoes do HL Coordenadas

Os dois executaveis incluem Coordenadas e Conferencia NFe nas portas 8766/8770.
Use somente uma edicao por PC ao mesmo tempo.

## HL_Coordenadas_Publicador_V7.exe

Envie somente para o PC cujo usuario Tiny pode consultar os logs de estoque.
A chave de publicacao do Supabase ja esta embutida: nao precisa criar JSON,
configurar variavel de ambiente ou enviar pasta junto.

1. Abra o executavel e conclua a autorizacao Tiny no navegador com o usuario correto.
2. Abra Mais > Atualizar vendas > Consultar logs e publicar.
3. Aguarde o progresso. Sao publicados apenas os agregados por SKU.

O publicador nao leva access_token ou refresh_token deste PC. A autorizacao e
feita no destino. Seus dados/token ficam em
`%LOCALAPPDATA%\HL_Coordenadas_V5_Publicador`.

O Tiny e acessado somente por GET nesta consulta de vendas. A publicacao dos
resumos e feita por POST na funcao restrita do Supabase. Nao usa notas fiscais.
Uma resposta 403 interrompe a atualizacao sem publicar zeros.

## HL_Coordenadas_Leitor_V7.exe

Envie aos demais PCs. Nao inclui a chave que permite publicar resumos e nao
abre a autorizacao Tiny automaticamente. Mesmo que haja chave de publicacao
antiga em arquivo ou variavel de ambiente, esta edicao ignora essa chave.

Caixas e mapa de vendas leem o resumo no Supabase sem login no Tiny.
O botao Atualizar das caixas nesta edicao so rele o resumo compartilhado.
Os demais recursos antigos que dependem do Tiny, como enviar localizacao e
imprimir etiquetas no ERP, continuam exigindo autorizacao propria quando usados.

## Cuidados

- A chave foi validada contra a funcao do Supabase sem gravar registros.
- Executaveis nao sao cofres: alguem com acesso ao Publicador pode extrair a
  chave embutida. Distribua-o apenas ao PC autorizado.
- O mapa usa os SKUs que ocupam cada caixa atualmente, nao a localizacao historica.
- Sem resumo e diferente de zero vendas. Itens ainda nao publicados aparecem sem resumo.
- Nenhuma importacao em massa comeca so por abrir o programa.
