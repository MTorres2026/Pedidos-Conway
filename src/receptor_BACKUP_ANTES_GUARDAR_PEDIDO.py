#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

HOST = "0.0.0.0"
PORT = 8765


class CineStockRequestHandler(BaseHTTPRequestHandler):

    def do_POST(self):

        if self.path != "/pedido":
            self.send_response(404)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.end_headers()

            respuesta = {
                "ok": False,
                "error": "Ruta no encontrada"
            }

            self.wfile.write(
                json.dumps(respuesta).encode("utf-8")
            )
            return

        content_length = self.headers.get("Content-Length")

        if not content_length:
            self.send_response(400)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )
            self.end_headers()

            respuesta = {
                "ok": False,
                "error": "Falta Content-Length"
            }

            self.wfile.write(
                json.dumps(respuesta).encode("utf-8")
            )
            return

        try:
            length = int(content_length)

            raw_body = self.rfile.read(length)

            data = json.loads(
                raw_body.decode("utf-8")
            )

            print()
            print("=" * 50)
            print("PEDIDO RECIBIDO DE CINESTOCK")
            print("=" * 50)

            print(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )
            )

            print("=" * 50)
            print()

            sys.stdout.flush()

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )

            self.end_headers()

            respuesta = {
                "ok": True
            }

            self.wfile.write(
                json.dumps(respuesta).encode("utf-8")
            )

        except (ValueError, json.JSONDecodeError) as e:

            self.send_response(400)

            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8"
            )

            self.end_headers()

            respuesta = {
                "ok": False,
                "error": f"JSON invalido: {str(e)}"
            }

            self.wfile.write(
                json.dumps(respuesta).encode("utf-8")
            )

    def do_GET(self):

        self.send_response(404)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.end_headers()

        respuesta = {
            "ok": False,
            "error": "Metodo GET no soportado. Usa POST /pedido"
        }

        self.wfile.write(
            json.dumps(respuesta).encode("utf-8")
        )

    def log_message(self, format, *args):

        print(
            f"[{self.log_date_time_string()}] "
            f"{self.client_address[0]} -> "
            f"{format % args}"
        )


def run_server():

    server_address = (HOST, PORT)

    httpd = HTTPServer(
        server_address,
        CineStockRequestHandler
    )

    print("=" * 60)
    print("  SERVIDOR RECEPTOR CINESTOCK INICIADO")
    print("=" * 60)
    print(
        f"  Escuchando en: "
        f"http://{HOST}:{PORT}/pedido"
    )
    print(
        "  Listo para recibir pedidos "
        "desde Motorola en red local"
    )
    print("=" * 60)

    try:

        httpd.serve_forever()

    except KeyboardInterrupt:

        print("\nServidor receptor detenido.")

        httpd.server_close()


if __name__ == "__main__":
    run_server()