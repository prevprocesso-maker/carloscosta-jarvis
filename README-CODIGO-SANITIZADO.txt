JARVIS CyberBot — pacote de código-fonte sanitizado

Este pacote contém o código-fonte e os componentes públicos do JARVIS CyberBot.
Credenciais, chaves de API, certificados privados, sessão do WhatsApp, banco de
mensagens, mídia, histórico pessoal, logs e caches da instalação original foram
removidos deliberadamente.

Antes de executar em outro computador:

1. Instale Python 3.11+ e as dependências listadas em requirements.txt.
2. Configure config/api_keys.json a partir de config/api_keys.example.json.
3. Instale as dependências Node em whatsapp_bridge com `npm install`.
4. Execute o JARVIS e faça um novo pareamento do WhatsApp e do Controle Remoto.

O arquivo JARVIS-CyberBot.exe é uma cópia do executável presente na instalação,
mas este pacote não é um instalador. O código editável está nos arquivos Python
e JavaScript incluídos.
