from http.server import HTTPServer, SimpleHTTPRequestHandler

server = HTTPServer(("0.0.0.0", 8000), SimpleHTTPRequestHandler)

print("Samobor bus map running at http://0.0.0.0:8000")

server.serve_forever()
