/**
 * The 3D view's element colours (the Jmol/CPK table of ffast/chemistry/atoms.py),
 * by atomic number; index 0 is "all atoms". A test keeps this copy in step with
 * the Python table.
 */

export const ELEMENT_COLOURS = [
  '#ffcc26', '#ffffff', '#d9ffff', '#cc80ff', '#c2ff00', '#ffb5b5', '#909090', '#2f50f8',
  '#ff0d0d', '#90df50', '#b3e2f5', '#ab5cf1', '#89ff00', '#bea6a6', '#efc79f', '#ff8000',
  '#ffff2f', '#1fef1f', '#80d1e2', '#8f40d3', '#3cff00', '#e6e6e6', '#bec2c6', '#a6a6ab',
  '#8999c6', '#9c79c6', '#df6633', '#ef909f', '#50d050', '#c78033', '#7c80af', '#c28f8f',
  '#668f8f', '#bc80e2', '#ffa000', '#a62929', '#5cb8d1', '#6f2daf', '#00ff00', '#93ffff',
  '#93dfdf', '#73c2c8', '#53b5b5', '#3a9e9e', '#238f8f', '#097c8b', '#006985', '#c0c0c0',
  '#ffd98f', '#a67573', '#668080', '#9e62b5', '#d37900', '#930093', '#429eaf', '#56168f',
  '#00c800', '#6fd3ff', '#ffffc6', '#d9ffc6', '#c6ffc6', '#a2ffc6', '#8fffc6', '#60ffc6',
  '#45ffc6', '#2fffc6', '#1fffc6', '#00ff9c', '#00e675', '#00d352', '#00be38', '#00ab23',
  '#4dc2ff', '#4da6ff', '#2093d5', '#257cab', '#256695', '#165386', '#d0d0df', '#ffd122',
  '#b8b8d0', '#a6534d', '#565860', '#9e4fb5', '#ab5c00', '#754f45', '#428295', '#420066',
  '#007c00', '#6fabf9', '#00b9ff', '#00a0ff', '#008fff', '#0080ff', '#006bff', '#535cf1',
  '#785ce2', '#894fe2', '#a036d3', '#b31fd3', '#b31fb9', '#b30da6', '#bc0d86', '#c60066',
  '#cc0058', '#d1004f', '#d90045', '#df0038', '#e6002d', '#eb0025',
];

/** The CPK colour of atomic number `z`, as a hex string. */
export function elementColour(z) {
  return ELEMENT_COLOURS[z] || ELEMENT_COLOURS[0];
}
