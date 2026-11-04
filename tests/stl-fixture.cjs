// A closed, outward-oriented unit tetrahedron; no supplier qualification.
module.exports = function tetrahedron() {
  const vertices = [[0,0,0],[1,0,0],[0,1,0],[0,0,1]];
  const faces = [[0,2,1],[0,1,3],[0,3,2],[1,2,3]];
  const raw = Buffer.alloc(84 + faces.length * 50);
  raw.writeUInt32LE(faces.length, 80);
  for (let i = 0; i < faces.length; i++) {
    faces[i].flatMap(index => vertices[index]).forEach((value, j) => raw.writeFloatLE(value, 84 + i * 50 + 12 + j * 4));
  }
  return raw;
};
