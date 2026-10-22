# Sudoku Board

The Sudoku Board is a 9x9 grid of tile objects that represents a Sudoku puzzle. It manages the state of the puzzle, validates moves according to Sudoku rules (no duplicate values in rows, columns, or 3x3 blocks), and distinguishes between starter tiles (non-writable hints) and user-writable tiles. The board supports reading tile values, writing valid values to writable tiles, erasing tiles, and managing user notes for possible values.

## Board Class

### Data Structure
- **Grid**: 2D array of Tile objects (9x9)
- **Indexing**: Rows, columns, and blocks are numbered 0-8 starting from the top left

### Methods

#### Initialization
```python
def __init__(self, board: list[list[int]]) -> None
```
Takes a starter board configuration and initializes the 9x9 grid with Tile objects. Any tile initialized with a value is marked as non-writable, and all empty tiles are marked as writable. After initialization, all empty tiles are automatically pre-populated with their candidate values (determined by getCandidates) in their notes lists.

#### Validation - Existence Checks
```python
def checkIfInRow(row: int, value: int) -> bool
```
Checks if a given value already exists in the specified row.

```python
def checkIfInColumn(column: int, value: int) -> bool
```
Checks if a given value already exists in the specified column.

```python
def checkIfInBlock(block: int, value: int) -> bool
```
Checks if a given value already exists in the specified block (0-8).

```python
def checkTileCandidate(row: int, column: int, candidate: int) -> bool
```
Returns True if the candidate is valid for the tile, i.e. it does not already exist in the tile's row, column, or block. Returns False if it conflicts.

#### Validation - Row/Column/Block
```python
def validateRow(row: int) -> bool
```
Returns True if the specified row contains no duplicate non-empty values.

```python
def validateColumn(column: int) -> bool
```
Returns True if the specified column contains no duplicate non-empty values.

```python
def validateBlock(block: int) -> bool
```
Returns True if the specified block contains no duplicate non-empty values.

#### Block Location Utility
```python
def locateBlock(row: int, column: int) -> int
```
Given a tile location (row, column), returns the block index (0-8) that the tile belongs to.

#### Full Board Validation
```python
def validateIncompleteBoard() -> bool
```
Validates the entire board while allowing empty tiles. Loops through all rows, columns, and blocks to check for any illegal duplicate values. Returns True if the board state is valid.

```python
def validatePuzzle() -> bool
```
Validates the entire board and ensures there are no empty tiles. Returns True if the puzzle is complete and valid.

#### Reading Structures
```python
def readRow(row: int) -> list[int | None]
```
Returns all tile values in the specified row in left-to-right order. Values are 1-9 or None for empty tiles.

```python
def readColumn(column: int) -> list[int | None]
```
Returns all tile values in the specified column in top-to-bottom order. Values are 1-9 or None for empty tiles.

```python
def readBlock(block: int) -> list[int | None]
```
Returns all tile values in the specified block (0-8) in reading order (left to right, top to bottom). Values are 1-9 or None for empty tiles.

#### Display
```python
def printBoard() -> None
```
Prints the board to the terminal in a human-readable format.
```python
def readBoard() -> None
```
Returns self.grid for the agent to read

---

## Tile Class

### Data

#### Value
- **Type**: `int | None`
- **Range**: 1-9 (or None for empty tiles)
- **Initialization**: None unless a value is provided in the starter board

#### Writable
- **Type**: `bool`
- **Initialization Logic**: 
  - Non-writable if the tile was initialized with a value in the starter board
  - Writable if the tile was empty (None) in the starter board

#### Notes
- **Type**: `list[int]`
- **Initialization**: Empty list
- **Purpose**: Stores user-entered possible values for the tile (manually populated by the user)

### Methods

#### Reading Values
```python
def readTile(row: int, column: int) -> int | None
```
Returns the current value of the tile at the specified location.

#### Writing Values
```python
def writeValue(row: int, column: int, value: int) -> bool
```
Writes a value to the tile if the tile is writable. Rejects values that are not integers in the range 1-9. Returns True if the write was successful, False otherwise.

#### Erasing Tiles
```python
def eraseTile(row: int, column: int) -> bool
```
Writes None to the tile's value if the tile is writable. Returns True if the erase was successful, False otherwise.

#### Notes Management
```python
def appendNote(row: int, column: int, values: list[int]) -> None
```
Appends values to the notes list for the tile at the specified location.

```python
def listNotes(row: int, column: int) -> list[int]
```
Returns the current notes list for the tile at the specified location.

```python
def removeNote(row: int, column: int, values: list[int]) -> bool
```
Removes values from the notes list for the tile at the specified location. Returns True if all values were successfully removed, False if any note didn't exist in the list.
