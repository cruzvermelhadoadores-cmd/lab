#!/usr/bin/env python3
"""
SSH Tunnel Manager - Gerencia sessões SSH através de VPN (SEM SUDO)
Versão simplificada com gerenciamento completo de VPN
"""
import re
import subprocess
import sys
import os
import json
import argparse
import getpass
from datetime import datetime
import signal
import time

class SSHTunnelManager:
    def __init__(self):
        self.home_dir = os.path.expanduser("~/sshtunnelpy")
        self.sessions_file = os.path.join(self.home_dir, "sessions.json")
        self.current_session_file = os.path.join(self.home_dir, "current_session.txt")
        self.log_dir = self.home_dir
        
        os.makedirs(self.home_dir, exist_ok=True)
        self.sessions = self.load_sessions()
        self.migrate_sessions()
    
    def load_sessions(self):
        if os.path.exists(self.sessions_file):
            with open(self.sessions_file, 'r') as f:
                return json.load(f)
        return []
    
    def save_sessions(self):
        with open(self.sessions_file, 'w') as f:
            json.dump(self.sessions, f, indent=2)
    
    def migrate_sessions(self):
        migrated = False
        for s in self.sessions:
            if 'vpn_host' not in s:
                s['vpn_host'] = s.get('ssh_host', 'unknown')
                s['vpn_port'] = '1194'
                s['vpn_user'] = 'unknown'
                s['vpn_pass'] = ''
                s['vpn_pid'] = None
                migrated = True
            if 'ovpn_file' not in s:
                s['ovpn_file'] = 'unknown.ovpn'
                migrated = True
        
        if migrated:
            self.save_sessions()
    
    def parse_ovpn(self, file):
        if not os.path.exists(file):
            print(f"❌ Arquivo não encontrado: {file}")
            return None, None
        
        with open(file, 'r') as f:
            content = f.read()
        
        remote = re.search(r'remote\s+(\S+)\s+(\d+)', content)
        host = remote.group(1) if remote else None
        port = remote.group(2) if remote else "1194"
        
        return host, port
    
    def parse_ssh_string(self, ssh_str):
        pattern = r'([^@]+)@([^:]+)(?::(\d+))?(?::(.+))?'
        match = re.match(pattern, ssh_str)
        
        if match:
            user = match.group(1)
            host = match.group(2)
            port = match.group(3) or "22"
            password = match.group(4)
            return user, host, port, password
        return None, None, "22", None
    
    def check_sshpass(self):
        try:
            subprocess.run(['which', 'sshpass'], check=True, capture_output=True)
            return True
        except:
            return False
    
    def is_vpn_active(self):
        """Verifica se VPN está ativa"""
        check = subprocess.run(['ip', 'addr', 'show', 'tun0'], 
                              capture_output=True, text=True)
        return check.returncode == 0
    
    def check_vpn_status(self):
        """Mostra status da VPN"""
        print("\n🔍 Status VPN:")
        
        if self.is_vpn_active():
            # Pegar informações da interface
            result = subprocess.run(['ip', 'addr', 'show', 'tun0'], 
                                  capture_output=True, text=True)
            
            # Extrair IP
            ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            vpn_ip = ip_match.group(1) if ip_match else "N/A"
            
            print(f"  ✅ VPN ATIVA")
            print(f"  📍 Interface: tun0")
            print(f"  🌐 IP VPN: {vpn_ip}")
            
            # Ver qual sessão está usando
            session = self.get_current_session()
            if session and session.get('vpn_pid'):
                print(f"  🔗 Sessão: {session['id']} ({session['name']})")
                print(f"  📦 Servidor: {session['vpn_host']}:{session['vpn_port']}")
                print(f"  👤 Usuário: {session['vpn_user']}")
                print(f"  🆔 PID: {session['vpn_pid']}")
            
            # Mostrar rotas VPN
            routes = subprocess.run(['ip', 'route', 'show', 'dev', 'tun0'], 
                                   capture_output=True, text=True)
            if routes.stdout:
                print(f"  🛣️  Rotas:")
                for line in routes.stdout.strip().split('\n')[:3]:  # Primeiras 3 rotas
                    print(f"     {line}")
            
            return True
        else:
            print("  ❌ VPN DESCONECTADA")
            print("  💡 Use: ./tunn -c <ID> para conectar")
            return False
    
    def stop_vpn(self):
        """Para apenas a VPN"""
        print("\n🛑 Parando VPN...")
        
        if not self.is_vpn_active():
            print("  ℹ️  VPN já está desconectada")
            return True
        
        session = self.get_current_session()
        
        # Tentar parar pelo PID salvo
        if session and session.get('vpn_pid'):
            try:
                subprocess.run(['sudo', 'kill', session['vpn_pid']], 
                             capture_output=True, timeout=5)
                print(f"  ✅ VPN parada (PID {session['vpn_pid']})")
                session['vpn_pid'] = None
                self.save_sessions()
                return True
            except:
                pass
        
        # Tentar parar todos os processos openvpn
        try:
            subprocess.run(['sudo', 'killall', 'openvpn'], 
                          capture_output=True, timeout=5)
            print("  ✅ VPN parada (killall openvpn)")
            
            # Aguardar interface sumir
            time.sleep(2)
            
            if not self.is_vpn_active():
                if session:
                    session['vpn_pid'] = None
                    self.save_sessions()
                return True
        except:
            pass
        
        print("  ⚠️  Não foi possível parar VPN")
        return False
    
    def restart_vpn(self):
        """Reinicia VPN da sessão atual"""
        session = self.get_current_session()
        if not session:
            print("❌ Nenhuma sessão ativa")
            return False
        
        print(f"\n🔄 Reiniciando VPN da sessão {session['id']}...")
        
        # Parar VPN atual
        self.stop_vpn()
        time.sleep(2)
        
        # Reconectar
        return self.connect_vpn(session)
    
    def connect_vpn(self, session):
        """Conecta apenas a VPN"""
        print(f"\n📡 Conectando VPN...")
        
        # Verificar openvpn
        try:
            subprocess.run(['which', 'openvpn'], check=True, capture_output=True)
        except:
            print("❌ OpenVPN não está instalado!")
            return False
        
        vpn_log = os.path.join(self.log_dir, f"vpn_{session['id']}.log")
        
        # Criar arquivo de credenciais
        auth_file = os.path.join(self.home_dir, f"vpn_auth_{session['id']}.txt")
        with open(auth_file, 'w') as f:
            f.write(f"{session['vpn_user']}\n{session['vpn_pass']}\n")
        os.chmod(auth_file, 0o600)
        
        # Comando OpenVPN
        vpn_cmd = [
            'sudo', 'openvpn',
            '--config', session['ovpn_file'],
            '--auth-user-pass', auth_file,
            '--daemon',
            '--log', vpn_log,
            '--writepid', os.path.join(self.home_dir, f"vpn_{session['id']}.pid")
        ]
        
        try:
            result = subprocess.run(vpn_cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                print(f"❌ Erro ao iniciar VPN: {result.stderr}")
                return False
            
            print("⏳ Aguardando VPN conectar (15s)...")
            
            for i in range(15):
                time.sleep(1)
                if self.is_vpn_active():
                    print("✅ VPN conectada!")
                    
                    # Salvar PID
                    pid_file = os.path.join(self.home_dir, f"vpn_{session['id']}.pid")
                    if os.path.exists(pid_file):
                        with open(pid_file, 'r') as f:
                            session['vpn_pid'] = f.read().strip()
                    
                    self.save_sessions()
                    return True
            
            print("⚠️  VPN demorou demais para conectar")
            print(f"📝 Ver logs: tail -f {vpn_log}")
            return False
        
        except Exception as e:
            print(f"❌ Erro ao conectar VPN: {e}")
            return False
        
        finally:
            if os.path.exists(auth_file):
                os.remove(auth_file)
    
    def diagnose_network(self, session):
        """Diagnóstico de rede"""
        print("\n🔍 Diagnóstico de Rede:")
        
        # 1. Interface VPN
        print("  1️⃣ Interface tun0...")
        if not self.is_vpn_active():
            print("     ❌ tun0 não encontrada!")
            return False
        
        print("     ✅ tun0 ativa")
        result = subprocess.run(['ip', 'addr', 'show', 'tun0'], 
                               capture_output=True, text=True)
        ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
        if ip_match:
            print(f"     📍 IP: {ip_match.group(1)}")
        
        # 2. Roteamento
        print(f"\n  2️⃣ Rota para {session['ssh_host']}...")
        route = subprocess.run(['ip', 'route', 'get', session['ssh_host']], 
                              capture_output=True, text=True)
        if route.returncode == 0:
            if 'tun0' in route.stdout:
                print(f"     ✅ Passa pela VPN")
            else:
                print(f"     ⚠️  NÃO passa pela VPN!")
                print(f"     {route.stdout.strip()}")
        else:
            print(f"     ❌ Sem rota!")
            return False
        
        # 3. Ping
        print(f"\n  3️⃣ Ping {session['ssh_host']}...")
        ping = subprocess.run(['ping', '-c', '2', '-W', '3', session['ssh_host']], 
                             capture_output=True, text=True)
        if ping.returncode == 0:
            print(f"     ✅ Host responde")
        else:
            print(f"     ⚠️  Sem resposta (pode estar bloqueado)")
        
        # 4. Porta SSH
        print(f"\n  4️⃣ Porta SSH {session['ssh_port']}...")
        nc = subprocess.run(['timeout', '5', 'nc', '-zv', 
                            session['ssh_host'], session['ssh_port']], 
                           capture_output=True, text=True)
        if nc.returncode == 0 or 'succeeded' in nc.stderr.lower():
            print(f"     ✅ Porta acessível")
            return True
        else:
            print(f"     ❌ Porta BLOQUEADA!")
            print(f"     {nc.stderr.strip()}")
            return False
    
    def init_session(self, ovpn_file, vpn_user=None, vpn_pass=None, ssh_string=None):
        print(f"🔍 Analisando {ovpn_file}...")
        
        vpn_host, vpn_port = self.parse_ovpn(ovpn_file)
        if not vpn_host:
            print("❌ Não consegui ler o arquivo .ovpn")
            return False
        
        print(f"📡 Servidor VPN: {vpn_host}:{vpn_port}")
        
        ssh_user = None
        ssh_host = None
        ssh_port = "22"
        ssh_pass = None
        
        if ssh_string:
            ssh_user, ssh_host, ssh_port, ssh_pass = self.parse_ssh_string(ssh_string)
            print(f"🔐 SSH: {ssh_user}@{ssh_host}:{ssh_port}")
        
        if not vpn_user:
            print(f"\n📋 Credenciais VPN:")
            vpn_user = input("  Usuário VPN: ")
        if not vpn_pass:
            vpn_pass = getpass.getpass("  Senha VPN: ")
        
        if ssh_string and not ssh_host:
            print(f"\n❌ Formato SSH inválido. Use: user@host:port:password")
            return False
        
        if not ssh_host:
            print(f"\n📋 Credenciais SSH (servidor atrás da VPN):")
            ssh_host = input("  Host SSH: ")
            ssh_user = input(f"  Usuário SSH [{ssh_host}]: ") or getpass.getuser()
            ssh_port = input("  Porta SSH [22]: ") or "22"
        
        if not ssh_pass:
            ssh_pass = getpass.getpass(f"  Senha SSH [{ssh_user}@{ssh_host}]: ")
        
        session = {
            'id': len(self.sessions) + 1,
            'name': f"session_{len(self.sessions) + 1}",
            'created': datetime.now().isoformat(),
            'ovpn_file': os.path.abspath(ovpn_file),
            'vpn_host': vpn_host,
            'vpn_port': vpn_port,
            'vpn_user': vpn_user,
            'vpn_pass': vpn_pass,
            'ssh_host': ssh_host,
            'ssh_port': ssh_port,
            'ssh_user': ssh_user,
            'ssh_pass': ssh_pass,
            'vpn_pid': None,
            'ssh_pid': None,
            'status': 'disconnected',
            'control_socket': os.path.join(self.home_dir, f"ssh_control_{len(self.sessions) + 1}")
        }
        
        self.sessions.append(session)
        self.save_sessions()
        
        print(f"\n✅ Sessão {session['id']} criada!")
        print(f"📦 VPN: {vpn_user}@{vpn_host}:{vpn_port}")
        print(f"🔐 SSH: {ssh_user}@{ssh_host}:{ssh_port}")
        
        connect_now = input("\n🚀 Conectar agora? [S/n]: ").strip().lower()
        if connect_now != 'n':
            return self.connect_session(session['id'])
        
        return True
    
    def connect_session(self, session_id):
        """Conecta VPN e SSH (auto-detecta se VPN já está ativa)"""
        session = self.get_session(session_id)
        if not session:
            print(f"❌ Sessão {session_id} não encontrada")
            return False
        
        print(f"\n🔒 Conectando sessão {session_id}...")
        
        # Verificar sshpass
        if not self.check_sshpass():
            print("\n❌ sshpass não está instalado!")
            print("   Instale: sudo apt install sshpass")
            return False
        
        # Conectar VPN se não estiver ativa
        if not self.is_vpn_active():
            print("📡 VPN desconectada, conectando...")
            if not self.connect_vpn(session):
                print("❌ Falha ao conectar VPN")
                return False
        else:
            print("✅ VPN já está conectada")
        
        # Diagnóstico
        if not self.diagnose_network(session):
            print("\n❌ Diagnóstico falhou! Host SSH não acessível")
            return False
        
        # Conectar SSH
        print(f"\n🔐 Conectando SSH a {session['ssh_host']}...")
        
        ssh_log = os.path.join(self.log_dir, f"ssh_{session_id}.log")
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session_id}")
        
        with open(ssh_config, 'w') as f:
            f.write(f"Host tunnel{session_id}\n")
            f.write(f"    HostName {session['ssh_host']}\n")
            f.write(f"    Port {session['ssh_port']}\n")
            f.write(f"    User {session['ssh_user']}\n")
            f.write(f"    StrictHostKeyChecking no\n")
            f.write(f"    UserKnownHostsFile /dev/null\n")
            f.write(f"    ServerAliveInterval 60\n")
            f.write(f"    ServerAliveCountMax 3\n")
            f.write(f"    ConnectTimeout 15\n")
            f.write(f"    ControlMaster auto\n")
            f.write(f"    ControlPath {session['control_socket']}\n")
            f.write(f"    ControlPersist yes\n")
        
        # Testar SSH primeiro (modo interativo para debug)
        print("🔍 Testando autenticação SSH...")
        test_cmd = [
            'sshpass', '-p', session['ssh_pass'],
            'ssh',
            '-F', ssh_config,
            '-o', 'ConnectTimeout=10',
            '-o', 'BatchMode=no',
            f"tunnel{session_id}",
            'echo "OK"'
        ]
        
        test_result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)
        
        if test_result.returncode != 0:
            print(f"❌ Falha no teste de autenticação!")
            print(f"   Saída: {test_result.stderr[:200]}")
            
            # Tentar SSH manual para debug
            print("\n🔧 Tente manualmente:")
            print(f"   sshpass -p '{session['ssh_pass']}' ssh {session['ssh_user']}@{session['ssh_host']}")
            return False
        
        print("✅ Autenticação OK, criando túnel...")
        
        # Agora criar túnel persistente
        ssh_cmd = [
            'sshpass', '-p', session['ssh_pass'],
            'ssh', '-f', '-N',
            '-F', ssh_config,
            '-o', 'ExitOnForwardFailure=yes',
            f"tunnel{session_id}"
        ]
        
        print("⏳ Estabelecendo túnel SSH...")
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=20)
            
            with open(ssh_log, 'w') as f:
                f.write("=== SSH Connection Log ===\n")
                f.write(f"STDOUT:\n{result.stdout}\n")
                f.write(f"STDERR:\n{result.stderr}\n")
                f.write(f"Return code: {result.returncode}\n")
            
            time.sleep(2)
            
            check_cmd = ['ssh', '-F', ssh_config, '-O', 'check', f"tunnel{session_id}"]
            check = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if "Master running" in check.stderr or check.returncode == 0:
                session['status'] = 'connected'
                session['connected_at'] = datetime.now().isoformat()
                
                with open(self.current_session_file, 'w') as f:
                    f.write(str(session_id))
                
                self.save_sessions()
                
                print(f"\n✅ Sessão {session_id} conectada!")
                print(f"📡 VPN: {session['vpn_host']}")
                print(f"🔐 SSH: {session['ssh_user']}@{session['ssh_host']}")
                print(f"\n💡 Comandos:")
                print(f"   ./tunn --cmd 'comando'  - Executar comando")
                print(f"   ./tunn --vpn            - Status VPN")
                print(f"   ./tunn --stop           - Parar tudo")
                
                return True
            else:
                print(f"\n❌ Falha SSH")
                print(f"📝 Ver: cat {ssh_log}")
                return False
                
        except Exception as e:
            print(f"\n❌ Erro: {e}")
            return False
    
    def execute_command(self, cmd):
        session = self.get_current_session()
        if not session:
            print("❌ Nenhuma sessão ativa. Use: ./tunn -c <ID>")
            return False
        
        if session['status'] != 'connected':
            print(f"❌ Sessão {session['id']} não conectada")
            print("💡 Tente: ./tunn -c {session['id']}")
            return False
        
        print(f"🔧 Executando: {cmd}")
        print(f"   Sessão {session['id']} ({session['ssh_user']}@{session['ssh_host']})\n")
        
        output_log = os.path.join(self.log_dir, "output.log")
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session['id']}")
        
        # Tentar com ControlMaster primeiro
        ssh_cmd = ['ssh', '-F', ssh_config, f"tunnel{session['id']}", cmd]
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
            
            # Se falhar, tentar direto com sshpass
            if result.returncode != 0 and "Control socket" in result.stderr:
                print("⚠️  Túnel inativo, conectando direto...")
                
                direct_cmd = [
                    'sshpass', '-p', session['ssh_pass'],
                    'ssh',
                    '-o', 'StrictHostKeyChecking=no',
                    '-o', 'UserKnownHostsFile=/dev/null',
                    '-o', 'ConnectTimeout=10',
                    f"{session['ssh_user']}@{session['ssh_host']}",
                    '-p', session['ssh_port'],
                    cmd
                ]
                
                result = subprocess.run(direct_cmd, capture_output=True, text=True, timeout=60)
            
            output = result.stdout
            if result.stderr and "Warning" not in result.stderr:
                output += "\n[STDERR]\n" + result.stderr
            
            print("="*60)
            print(output)
            print("="*60 + "\n")
            
            with open(output_log, 'a') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n[{timestamp}] Session {session['id']}: {cmd}\n")
                f.write(output)
                f.write("\n" + "="*60 + "\n")
            
            print(f"📝 Log: {output_log}")
            return True
            
        except subprocess.TimeoutExpired:
            print("⏱️  Timeout (60s)")
            return False
        except Exception as e:
            print(f"❌ Erro: {e}")
            return False
    
    def stop_session(self):
        """Para SSH e VPN"""
        session = self.get_current_session()
        if not session:
            print("❌ Nenhuma sessão ativa")
            return False
        
        print(f"\n🛑 Parando sessão {session['id']}...")
        
        # Parar SSH
        ssh_config = os.path.join(self.home_dir, f"ssh_config_{session['id']}")
        if os.path.exists(ssh_config):
            try:
                subprocess.run(['ssh', '-F', ssh_config, '-O', 'exit', 
                              f"tunnel{session['id']}"], 
                              capture_output=True, timeout=5)
                print("✅ SSH desconectado")
            except:
                pass
        
        # Parar VPN
        self.stop_vpn()
        
        # Limpar
        session['status'] = 'disconnected'
        session['vpn_pid'] = None
        session['ssh_pid'] = None
        self.save_sessions()
        
        for f in [ssh_config, session.get('control_socket', ''), self.current_session_file]:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
        
        print(f"\n✅ Sessão {session['id']} parada")
        return True
    
    def list_sessions(self):
        if not self.sessions:
            print("📭 Nenhuma sessão")
            return
        
        current_id = self.get_current_session_id()
        
        print("\n📋 Sessões:\n")
        print(f"{'ID':<4} {'Status':<12} {'VPN':<30} {'SSH':<30}")
        print("-" * 80)
        
        for s in self.sessions:
            status = s.get('status', 'unknown')
            if s['id'] == current_id:
                status += " ⭐"
            
            vpn = f"{s.get('vpn_host', 'N/A')}:{s.get('vpn_port', 'N/A')}"
            ssh = f"{s.get('ssh_user', 'N/A')}@{s.get('ssh_host', 'N/A')}"
            
            print(f"{s['id']:<4} {status:<12} {vpn:<30} {ssh:<30}")
        
        print()
    
    def remove_session(self, session_id):
        session = self.get_session(session_id)
        if not session:
            print(f"❌ Sessão {session_id} não encontrada")
            return False
        
        if session.get('status') == 'connected':
            current = self.get_current_session()
            if current and current['id'] == session_id:
                self.stop_session()
        
        self.sessions = [s for s in self.sessions if s['id'] != session_id]
        self.save_sessions()
        
        print(f"✅ Sessão {session_id} removida")
        return True
    
    def get_session(self, session_id):
        for s in self.sessions:
            if s['id'] == session_id:
                return s
        return None
    
    def get_current_session_id(self):
        if os.path.exists(self.current_session_file):
            with open(self.current_session_file, 'r') as f:
                return int(f.read().strip())
        return None
    
    def get_current_session(self):
        sid = self.get_current_session_id()
        return self.get_session(sid) if sid else None

def main():
    parser = argparse.ArgumentParser(
        description='SSH Tunnel Manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  ./tunn --init vpn.ovpn --user="USER" --pass="PASS" --ssh="user@host:22:pass"
  ./tunn -c 1              # Conectar sessão 1
  ./tunn --cmd "ls -la"    # Executar comando
  ./tunn -l                # Listar sessões
  ./tunn --vpn             # Status VPN
  ./tunn --vpn-stop        # Parar VPN
  ./tunn --vpn-restart     # Reiniciar VPN
  ./tunn --stop            # Parar tudo
        """
    )
    
    parser.add_argument('--init', metavar='OVPN', help='Criar sessão')
    parser.add_argument('--user', help='Usuário VPN')
    parser.add_argument('--pass', dest='password', help='Senha VPN')
    parser.add_argument('--ssh', help='SSH: user@host:port:password')
    parser.add_argument('-c', type=int, metavar='ID', help='Conectar sessão')
    parser.add_argument('--cmd', help='Executar comando')
    parser.add_argument('--stop', action='store_true', help='Parar tudo')
    parser.add_argument('-l', '--list', action='store_true', help='Listar sessões')
    parser.add_argument('--rm', type=int, metavar='ID', help='Remover sessão')
    parser.add_argument('--vpn', action='store_true', help='Status VPN')
    parser.add_argument('--vpn-stop', action='store_true', help='Parar VPN')
    parser.add_argument('--vpn-restart', action='store_true', help='Reiniciar VPN')
    parser.add_argument('--test-ssh', type=int, metavar='ID', help='Testar SSH direto')
    
    args = parser.parse_args()
    
    manager = SSHTunnelManager()
    
    if args.init:
        manager.init_session(args.init, args.user, args.password, args.ssh)
    elif args.c:
        manager.connect_session(args.c)
    elif args.cmd:
        manager.execute_command(args.cmd)
    elif args.stop:
        manager.stop_session()
    elif args.list:
        manager.list_sessions()
    elif args.rm:
        manager.remove_session(args.rm)
    elif args.vpn:
        manager.check_vpn_status()
    elif args.vpn_stop:
        manager.stop_vpn()
    elif args.vpn_restart:
        manager.restart_vpn()
    elif args.test_ssh:
        session = manager.get_session(args.test_ssh)
        if session:
            print(f"\n🧪 Testando SSH para sessão {args.test_ssh}...\n")
            cmd = [
                'sshpass', '-p', session['ssh_pass'],
                'ssh',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ConnectTimeout=10',
                f"{session['ssh_user']}@{session['ssh_host']}",
                '-p', session['ssh_port'],
                'hostname && whoami'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            print(result.stdout)
            if result.stderr and "Warning" not in result.stderr:
                print(f"STDERR: {result.stderr}")
            print(f"\n{'✅ SSH OK!' if result.returncode == 0 else '❌ SSH FALHOU!'}")
        else:
            print(f"❌ Sessão {args.test_ssh} não encontrada")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()